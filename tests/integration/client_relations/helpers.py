#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from typing import Tuple
import logging
from pathlib import Path
import yaml
import subprocess

from tenacity import (
    retry,
    stop_after_attempt,
    wait_fixed,
)

from ..helpers import (
    MONGOS_APP_NAME,
    MongoClient,
    get_application_relation_data,
    get_secret_data,
)

from pytest_operator.plugin import OpsTest


PORT_MAPPING_INDEX = 4

logger = logging.getLogger(__name__)


MONGODB_CHARM_NAME = "mongodb-k8s"
CONFIG_SERVER_APP_NAME = "config-server"
SHARD_APP_NAME = "shard0"
MONGOS_PORT = 27018
SHARD_REL_NAME = "sharding"
CONFIG_SERVER_REL_NAME = "config-server"
CLUSTER_REL_NAME = "cluster"

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


@retry(stop=stop_after_attempt(10), wait=wait_fixed(15), reraise=True)
async def get_mongos_user_password(
    ops_test: OpsTest, app_name=MONGOS_APP_NAME, relation_name="cluster"
) -> Tuple[str, str]:
    secret_uri = await get_application_relation_data(
        ops_test, app_name, relation_name=relation_name, key="secret-user"
    )
    assert secret_uri, "No secret URI found"

    secret_data = await get_secret_data(ops_test, secret_uri)

    return secret_data.get("username"), secret_data.get("password")


@retry(stop=stop_after_attempt(10), wait=wait_fixed(15), reraise=True)
async def get_client_connection_string(
    ops_test: OpsTest, app_name=MONGOS_APP_NAME, relation_name="cluster"
) -> Tuple[str, str]:
    secret_uri = await get_application_relation_data(
        ops_test, app_name, relation_name=relation_name, key="secret-user"
    )
    assert secret_uri, "No secret URI found"

    secret_data = await get_secret_data(ops_test, secret_uri)
    return secret_data.get("uris")


def is_relation_joined(ops_test: OpsTest, endpoint_one: str, endpoint_two: str) -> bool:
    """Check if a relation is joined.

    Args:
        ops_test: The ops test object passed into every test case
        endpoint_one: The first endpoint of the relation
        endpoint_two: The second endpoint of the relation
    """
    for rel in ops_test.model.relations:
        endpoints = [endpoint.name for endpoint in rel.endpoints]
        if endpoint_one in endpoints and endpoint_two in endpoints:
            return True
    return False


def get_port_from_node_port(ops_test: OpsTest, node_port_name: str) -> str:
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


async def assert_all_unit_node_ports_available(ops_test: OpsTest):
    """Assert all ports available in mongos deployment."""
    for unit_id in range(len(ops_test.model.applications[MONGOS_APP_NAME].units)):
        assert_node_port_available(
            ops_test, node_port_name=f"{MONGOS_APP_NAME}-{unit_id}-external"
        )

        exposed_node_port = get_port_from_node_port(
            ops_test, node_port_name=f"{MONGOS_APP_NAME}-{unit_id}-external"
        )
        public_k8s_ip = get_public_k8s_ip()
        username, password = await get_mongos_user_password(ops_test, MONGOS_APP_NAME)
        external_mongos_client = MongoClient(
            f"mongodb://{username}:{password}@{public_k8s_ip}:{exposed_node_port}"
        )
        external_mongos_client.admin.command("usersInfo")
        external_mongos_client.close()


def get_public_k8s_ip() -> str:
    result = subprocess.run(
        "kubectl get nodes", shell=True, capture_output=True, text=True
    )

    if result.returncode:
        logger.info("failed to retrieve public facing k8s IP error: %s", result.stderr)
        assert False, "failed to retrieve public facing k8s IP"

    if len(result.stdout.splitlines()) < 2:
        logger.info("No entries for public facing k8s IP, : %s", result.stdout)
        assert False, "failed to retrieve public facing k8s IP"

    # port information is the first item of the last line
    port_mapping = result.stdout.splitlines()[-1].split()[0]

    # port mapping is of the form ip-172-31-18-133
    return port_mapping.split("ip-")[1].replace("-", ".")
