#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import subprocess
import logging

from typing import Any, Dict, List, Optional
from dateutil.parser import parse
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)

PORT_MAPPING_INDEX = 4
MONGOS_APP_NAME = "mongos"


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


def get_port_from_node_port(ops_test: OpsTest, node_port_name: str) -> None:
    node_port_cmd = f"kubectl get svc  -n  {ops_test.model.name} |  grep NodePort | grep {node_port_name}"
    result = subprocess.run(node_port_cmd, shell=True, capture_output=True, text=True)
    if result.returncode:
        logger.info("was not able to find nodeport")
        assert False, f"Command: {node_port_cmd} to find node port failed."

    assert (
        len(result.stdout.splitlines()) > 0
    ), "No port information available for expected service"

    # port information is available at PORT_MAPPING_INDEX
    port_mapping = result.stdout.split()[PORT_MAPPING_INDEX]

    # port information is of the form 27018:30259/TCP
    return port_mapping.split(":")[1].split("/")[0]


def assert_node_port_available(ops_test: OpsTest, node_port_name: str) -> None:
    assert get_port_from_node_port(
        ops_test, node_port_name
    ), "No port information for expected service"


def get_public_k8s_ip() -> str:
    result = subprocess.run(
        "kubectl get nodes", shell=True, capture_output=True, text=True
    )

    if result.returncode:
        logger.info("failed to retrieve public facing k8s IP")
        assert False, "failed to retrieve public facing k8s IP"

    # port information is the first item of the last line
    port_mapping = result.stdout.splitlines()[-1].split()[0]

    # port mapping is of the form ip-172-31-18-133
    return port_mapping.split("ip-")[1].replace("-", ".")
