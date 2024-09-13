#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import logging

from typing import Any, Dict, List, Optional, Tuple

from pathlib import Path
import yaml
from pymongo import MongoClient

from dateutil.parser import parse
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed
from tenacity import (
    RetryError,
)

logger = logging.getLogger(__name__)

PORT_MAPPING_INDEX = 4

MONGOS_APP_NAME = "mongos-k8s"
MONGODB_CHARM_NAME = "mongodb-k8s"
CONFIG_SERVER_APP_NAME = "config-server"
SHARD_APP_NAME = "shard0"
MONGOS_PORT = 27018
SHARD_REL_NAME = "sharding"
CONFIG_SERVER_REL_NAME = "config-server"
CLUSTER_REL_NAME = "cluster"

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


class Status:
    """Model class for status."""

    def __init__(self, value: str, since: str, message: Optional[str] = None):
        self.value = value
        self.since = parse(since, ignoretz=True)
        self.message = message


class Unit:
    """Model class for a Unit, with properties widely used."""

    def __init__(
        self,
        id: int,
        name: str,
        ip: str,
        hostname: str,
        is_leader: bool,
        workload_status: Status,
        agent_status: Status,
        app_status: Status,
    ):
        self.id = id
        self.name = name
        self.ip = ip
        self.hostname = hostname
        self.is_leader = is_leader
        self.workload_status = workload_status
        self.agent_status = agent_status
        self.app_status = app_status

    def dump(self) -> Dict[str, Any]:
        """To json."""
        result = {}
        for key, val in vars(self).items():
            result[key] = vars(val) if isinstance(val, Status) else val
        return result


async def get_application_units(ops_test: OpsTest, app: str) -> List[Unit]:
    """Get fully detailed units of an application."""
    # Juju incorrectly reports the IP addresses after the network is restored this is reported as a
    # bug here: https://github.com/juju/python-libjuju/issues/738. Once this bug is resolved use of
    # `get_unit_ip` should be replaced with `.public_address`
    raw_app = await get_raw_application(ops_test, app)
    units = []
    for u_name, unit in raw_app["units"].items():
        unit_id = int(u_name.split("/")[-1])
        if not unit.get("address", False):
            # unit not ready yet...
            continue

        unit = Unit(
            id=unit_id,
            name=u_name.replace("/", "-"),
            ip=unit["address"],
            hostname=await get_unit_hostname(ops_test, unit_id, app),
            is_leader=unit.get("leader", False),
            workload_status=Status(
                value=unit["workload-status"]["current"],
                since=unit["workload-status"]["since"],
                message=unit["workload-status"].get("message"),
            ),
            agent_status=Status(
                value=unit["juju-status"]["current"],
                since=unit["juju-status"]["since"],
            ),
            app_status=Status(
                value=raw_app["application-status"]["current"],
                since=raw_app["application-status"]["since"],
                message=raw_app["application-status"].get("message"),
            ),
        )

        units.append(unit)

    return units


async def check_all_units_blocked_with_status(
    ops_test: OpsTest, db_app_name: str, status: Optional[str]
) -> None:
    # this is necessary because ops_model.units does not update the unit statuses
    for unit in await get_application_units(ops_test, db_app_name):
        assert (
            unit.workload_status.value == "blocked"
        ), f"unit {unit.name} not in blocked state, in {unit.workload_status.value}"
        if status:
            assert (
                unit.workload_status.message == status
            ), f"unit {unit.name} not in blocked state, in {unit.workload_status.value}"


async def get_unit_hostname(ops_test: OpsTest, unit_id: int, app: str) -> str:
    """Get the hostname of a specific unit."""
    _, hostname, _ = await ops_test.juju("ssh", f"{app}/{unit_id}", "hostname")
    return hostname.strip()


async def get_raw_application(ops_test: OpsTest, app: str) -> Dict[str, Any]:
    """Get raw application details."""
    ret_code, stdout, stderr = await ops_test.juju(
        *f"status --model {ops_test.model.info.name} {app} --format=json".split()
    )
    if ret_code != 0:
        logger.error(f"Invalid return [{ret_code=}]: {stderr=}")
        raise Exception(f"[{ret_code=}] {stderr=}")
    return json.loads(stdout)["applications"][app]


async def wait_for_mongos_units_blocked(
    ops_test: OpsTest, db_app_name: str, status: Optional[str] = None, timeout=20
) -> None:
    """Waits for units of MongoDB to be in the blocked state.

    This is necessary because the MongoDB app can report a different status than the units.
    """
    hook_interval_key = "update-status-hook-interval"
    try:
        old_interval = (await ops_test.model.get_config())[hook_interval_key]
        await ops_test.model.set_config({hook_interval_key: "1m"})
        for attempt in Retrying(
            stop=stop_after_delay(timeout), wait=wait_fixed(1), reraise=True
        ):
            with attempt:
                await check_all_units_blocked_with_status(ops_test, db_app_name, status)
    finally:
        await ops_test.model.set_config({hook_interval_key: old_interval})


async def deploy_cluster_components(ops_test: OpsTest) -> None:
    """Deploys all cluster components and waits for idle."""
    mongos_charm = await ops_test.build_charm(".")
    resources = {
        "mongodb-image": METADATA["resources"]["mongodb-image"]["upstream-source"]
    }
    await ops_test.model.deploy(
        mongos_charm,
        resources=resources,
        application_name=MONGOS_APP_NAME,
        series="jammy",
    )

    await ops_test.model.deploy(
        MONGODB_CHARM_NAME,
        application_name=CONFIG_SERVER_APP_NAME,
        channel="6/edge",
        config={"role": "config-server"},
    )
    await ops_test.model.deploy(
        MONGODB_CHARM_NAME,
        application_name=SHARD_APP_NAME,
        channel="6/edge",
        config={"role": "shard"},
    )

    await ops_test.model.wait_for_idle(
        apps=[MONGOS_APP_NAME, SHARD_APP_NAME, CONFIG_SERVER_APP_NAME],
        idle_period=10,
        raise_on_blocked=False,
        raise_on_error=False,  # Removed this once DPE-4996 is resolved.
    )


async def build_cluster(ops_test: OpsTest) -> None:
    """Builds the cluster by integrating the components."""
    # prepare sharded cluster
    await ops_test.model.wait_for_idle(
        apps=[CONFIG_SERVER_APP_NAME, SHARD_APP_NAME],
        idle_period=10,
        raise_on_blocked=False,
        raise_on_error=False,  # Removed this once DPE-4996 is resolved.
    )
    await ops_test.model.integrate(
        f"{SHARD_APP_NAME}:{SHARD_REL_NAME}",
        f"{CONFIG_SERVER_APP_NAME}:{CONFIG_SERVER_REL_NAME}",
    )
    await ops_test.model.wait_for_idle(
        apps=[CONFIG_SERVER_APP_NAME, SHARD_APP_NAME],
        idle_period=20,
        raise_on_blocked=False,
        raise_on_error=False,  # https://github.com/canonical/mongodb-k8s-operator/issues/301
    )

    # connect sharded cluster to mongos
    await ops_test.model.integrate(
        f"{MONGOS_APP_NAME}:{CLUSTER_REL_NAME}",
        f"{CONFIG_SERVER_APP_NAME}:{CLUSTER_REL_NAME}",
    )
    await ops_test.model.wait_for_idle(
        apps=[CONFIG_SERVER_APP_NAME, SHARD_APP_NAME, MONGOS_APP_NAME],
        idle_period=20,
        status="active",
        raise_on_error=False,  # Removed this once DPE-4996 is resolved.
    )


async def get_application_name(ops_test: OpsTest, application_name: str) -> str:
    """Returns the Application in the juju model that matches the provided application name.

    This enables us to retrieve the name of the deployed application in an existing model, while
     ignoring some test specific applications.
    Note: if multiple applications with the application name exist, the first one found will be
     returned.
    """
    status = await ops_test.model.get_status()

    for application in ops_test.model.applications:
        # note that format of the charm field is not exactly "mongodb" but instead takes the form
        # of `local:focal/mongodb-6`
        if application_name in status["applications"][application]["charm"]:
            return application

    return None


async def get_address_of_unit(
    ops_test: OpsTest, unit_id: int, app_name: str = MONGOS_APP_NAME
) -> str:
    """Retrieves the address of the unit based on provided id."""
    status = await ops_test.model.get_status()
    return status["applications"][app_name]["units"][f"{app_name}/{unit_id}"]["address"]


async def get_secret_data(ops_test, secret_uri) -> Dict:
    """Returns secret relation data."""
    secret_unique_id = secret_uri.split("/")[-1]
    complete_command = f"show-secret {secret_uri} --reveal --format=json"
    _, stdout, _ = await ops_test.juju(*complete_command.split())
    return json.loads(stdout)[secret_unique_id]["content"]["Data"]


async def get_application_relation_data(
    ops_test: OpsTest,
    application_name: str,
    relation_name: str,
    key: str,
    relation_id: str = None,
    relation_alias: str = None,
) -> Optional[str]:
    """Get relation data for an application.

    Args:
        ops_test: The ops test framework instance
        application_name: The name of the application
        relation_name: name of the relation to get connection data from
        key: key of data to be retrieved
        relation_id: id of the relation to get connection data from
        relation_alias: alias of the relation (like a connection name)
            to get connection data from
    Returns:
        the that that was requested or None
            if no data in the relation
    Raises:
        ValueError if it's not possible to get application unit data
            or if there is no data for the particular relation endpoint
            and/or alias.
    """
    unit = ops_test.model.applications[application_name].units[0]
    raw_data = (await ops_test.juju("show-unit", unit.name))[1]
    if not raw_data:
        raise ValueError(f"no unit info could be grabbed for { unit.name}")
    data = yaml.safe_load(raw_data)
    # Filter the data based on the relation name.
    relation_data = [
        v for v in data[unit.name]["relation-info"] if v["endpoint"] == relation_name
    ]

    if relation_id:
        # Filter the data based on the relation id.
        relation_data = [v for v in relation_data if v["relation-id"] == relation_id]

    if relation_alias:
        # Filter the data based on the cluster/relation alias.
        relation_data = [
            v
            for v in relation_data
            if json.loads(v["application-data"]["data"])["alias"] == relation_alias
        ]

    if len(relation_data) == 0:
        raise ValueError(
            f"no relation data could be grabbed on relation with endpoint {relation_name} and alias {relation_alias}"
        )

    return relation_data[0]["application-data"].get(key)


async def get_mongos_user_password(
    ops_test: OpsTest, app_name=MONGOS_APP_NAME, relation_name="cluster"
) -> Tuple[str, str]:
    secret_uri = await get_application_relation_data(
        ops_test, app_name, relation_name=relation_name, key="secret-user"
    )

    secret_data = await get_secret_data(ops_test, secret_uri)
    return secret_data.get("username"), secret_data.get("password")


async def check_mongos(
    ops_test: OpsTest,
    unit_id: int = 0,
    auth: bool = True,
    app_name=MONGOS_APP_NAME,
    uri: str = None,
) -> bool:
    """Returns True if mongos is running on the provided unit."""
    mongos_client = await get_direct_mongos_client(
        ops_test, unit_id, auth, app_name, uri
    )
    try:
        # wait 10 seconds in case the daemon was just started
        for attempt in Retrying(stop=stop_after_delay(10)):
            with attempt:
                mongos_client.admin.command("ping")

    except RetryError:
        return False
    finally:
        mongos_client.close()

    return True


async def get_mongos_uri(
    ops_test: OpsTest, unit_id: int, auth: bool = True, app_name=MONGOS_APP_NAME
):
    mongos_host = await get_address_of_unit(ops_test, unit_id)

    if not auth:
        return f"mongodb://{mongos_host}:{MONGOS_PORT}"
    else:
        username, password = await get_mongos_user_password(ops_test, app_name)
        return f"mongodb://{username}:{password}@{mongos_host}:{MONGOS_PORT}"


async def get_direct_mongos_client(
    ops_test: OpsTest,
    unit_id: int = 0,
    auth: bool = True,
    app_name: str = MONGOS_APP_NAME,
    uri: str = None,
) -> MongoClient:
    """Returns a direct mongodb client potentially passing over some of the units."""
    mongos_uri = uri or await get_mongos_uri(ops_test, unit_id, auth, app_name)
    return MongoClient(mongos_uri, directConnection=True)
