#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.


import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    MONGOS_APP_NAME,
    MongoClient,
    build_cluster,
    deploy_cluster_components,
    get_mongos_user_password,
    MongoClient,
)

from .helpers import (
    assert_node_port_available,
    get_port_from_node_port,
    get_public_k8s_ip,
)

TEST_USER_NAME = "TestUserName1"
TEST_USER_PWD = "Test123"
TEST_DB_NAME = "my-test-db"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy a sharded cluster."""
    await deploy_cluster_components(ops_test)
    await build_cluster(ops_test)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_external_connections(ops_test: OpsTest) -> None:
    """Tests that mongos is accessible externally."""
    configuration_parameters = {"expose-external": "nodeport"}

    # apply new configuration options
    await ops_test.model.applications[MONGOS_APP_NAME].set_config(configuration_parameters)
    for unit_id in range(len(ops_test.model.applications[MONGOS_APP_NAME].units)):
        assert_node_port_available(
            ops_test, node_port_name=f"{MONGOS_APP_NAME}-{unit_id}-external"
        )

        exposed_node_port = get_port_from_node_port(ops_test, node_port_name="mongos-k8s-nodeport")
        public_k8s_ip = get_public_k8s_ip()
        username, password = await get_mongos_user_password(ops_test, MONGOS_APP_NAME)
        external_mongos_client = MongoClient(
            f"mongodb://{username}:{password}@{public_k8s_ip}:{exposed_node_port}"
        )
        external_mongos_client.admin.command("usersInfo")
        external_mongos_client.close()
