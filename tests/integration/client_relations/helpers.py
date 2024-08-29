#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import subprocess
import logging

from ..helpers import (
    MONGOS_APP_NAME,
    get_mongos_user_password,
    MongoClient,
)

from pytest_operator.plugin import OpsTest
from pymongo.errors import ServerSelectionTimeoutError

logger = logging.getLogger(__name__)

PORT_MAPPING_INDEX = 4


def get_node_port_info(ops_test, node_port_name: str):
    node_port_cmd = f"kubectl get svc  -n  {ops_test.model.name} |  grep NodePort | grep {node_port_name}"
    return subprocess.run(node_port_cmd, shell=True, capture_output=True, text=True)


def has_node_port(ops_test: OpsTest, node_port_name: str) -> None:
    result = get_node_port_info(ops_test, node_port_name)
    return len(result.stdout.splitlines()) > 0


def get_port_from_node_port(ops_test: OpsTest, node_port_name: str) -> str:
    result = get_node_port_info(ops_test, node_port_name)

    assert (
        len(result.stdout.splitlines()) > 0
    ), "No port information available for expected service"

    # port information is available at PORT_MAPPING_INDEX
    port_mapping = result.stdout.split()[PORT_MAPPING_INDEX]

    # port information is of the form 27018:30259/TCP
    return port_mapping.split(":")[1].split("/")[0]


def assert_node_port_availablity(
    ops_test: OpsTest, node_port_name: str, available: bool = True
) -> None:
    incorrect_availablity = "not available" if available else "is available"
    assert (
        has_node_port(ops_test, node_port_name) == available
    ), f"Port information {incorrect_availablity} for service"


async def assert_all_unit_node_ports_available(ops_test: OpsTest):
    """Assert all ports available in mongos deployment."""
    for unit_id in range(len(ops_test.model.applications[MONGOS_APP_NAME].units)):
        assert_node_port_availablity(
            ops_test, node_port_name=f"{MONGOS_APP_NAME}-{unit_id}-external"
        )

        exposed_node_port = get_port_from_node_port(
            ops_test, node_port_name=f"{MONGOS_APP_NAME}-{unit_id}-external"
        )

        assert await is_external_mongos_client_reachble(
            ops_test, exposed_node_port
        ), "client is not reachable"


async def is_external_mongos_client_reachble(
    ops_test: OpsTest, exposed_node_port: str
) -> bool:
    """Returns True if the mongos client is reachable on the provided node port via the k8s ip."""
    public_k8s_ip = get_public_k8s_ip()
    username, password = await get_mongos_user_password(ops_test, MONGOS_APP_NAME)
    try:
        external_mongos_client = MongoClient(
            f"mongodb://{username}:{password}@{public_k8s_ip}:{exposed_node_port}"
        )
        external_mongos_client.admin.command("usersInfo")
    except ServerSelectionTimeoutError:
        return False
    finally:
        external_mongos_client.close()

    return True


async def assert_all_unit_node_ports_are_unavailable(ops_test: OpsTest):
    """Assert all ports available in mongos deployment."""
    for unit_id in range(len(ops_test.model.applications[MONGOS_APP_NAME].units)):
        assert_node_port_availablity(
            ops_test,
            node_port_name=f"{MONGOS_APP_NAME}-{unit_id}-external",
            available=False,
        )


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
