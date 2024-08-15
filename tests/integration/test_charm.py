#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.


import pytest
from pytest_operator.plugin import OpsTest

from .helpers import (
    assert_node_port_available,
    check_mongos,
    get_direct_mongos_client,
    get_public_k8s_ip,
    get_mongos_user_password,
    get_address_of_unit,
    get_port_from_node_port,
    wait_for_mongos_units_blocked,
    SHARD_APP_NAME,
    CONFIG_SERVER_APP_NAME,
    CLUSTER_REL_NAME,
    MONGOS_APP_NAME,
    MONGOS_PORT,
    SHARD_REL_NAME,
    CONFIG_SERVER_REL_NAME,
    deploy_cluster_components,
    MongoClient,
)

TEST_USER_NAME = "TestUserName1"
TEST_USER_PWD = "Test123"
TEST_DB_NAME = "my-test-db"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy a sharded cluster."""
    await deploy_cluster_components(ops_test)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_waits_for_config_server(ops_test: OpsTest) -> None:
    """Verifies that the application and unit are active."""

    # verify that Charmed Mongos is blocked and reports incorrect credentials
    await wait_for_mongos_units_blocked(
        ops_test,
        MONGOS_APP_NAME,
        status="Missing relation to config-server.",
        timeout=300,
    )


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_starts_with_config_server(ops_test: OpsTest) -> None:
    # prepare sharded cluster
    await ops_test.model.wait_for_idle(
        apps=[CONFIG_SERVER_APP_NAME, SHARD_APP_NAME],
        idle_period=10,
        raise_on_blocked=False,
    )
    await ops_test.model.integrate(
        f"{SHARD_APP_NAME}:{SHARD_REL_NAME}",
        f"{CONFIG_SERVER_APP_NAME}:{CONFIG_SERVER_REL_NAME}",
    )
    await ops_test.model.wait_for_idle(
        apps=[CONFIG_SERVER_APP_NAME, SHARD_APP_NAME],
        idle_period=20,
        raise_on_blocked=False,
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
    )

    mongos_running = await check_mongos(ops_test, unit_id=0, auth=False)
    assert mongos_running, "Mongos is not currently running."


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_external_connections(ops_test: OpsTest) -> None:
    """Tests that mongos is accessible externally."""
    assert_node_port_available(ops_test, node_port_name="mongos-k8s-nodeport")
    exposed_node_port = get_port_from_node_port(ops_test, node_port_name="mongos-k8s-nodeport")
    public_k8s_ip = get_public_k8s_ip()
    username, password = await get_mongos_user_password(ops_test, MONGOS_APP_NAME)
    external_mongos_client = MongoClient(
        f"mongodb://{username}:{password}@{public_k8s_ip}:{exposed_node_port}"
    )
    external_mongos_client.admin.command("usersInfo")
    external_mongos_client.close()


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_has_user(ops_test: OpsTest) -> None:
    mongos_running = await check_mongos(ops_test, unit_id=0, auth=True)
    assert mongos_running, "Mongos is not currently running."


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_user_with_extra_roles(ops_test: OpsTest) -> None:
    mongos_client = await get_direct_mongos_client(
        ops_test, unit_id=0, auth=True, app_name=MONGOS_APP_NAME
    )
    mongos_client.admin.command(
        "createUser",
        TEST_USER_NAME,
        pwd=TEST_USER_PWD,
        roles=[{"role": "readWrite", "db": TEST_DB_NAME}],
        mechanisms=["SCRAM-SHA-256"],
    )
    mongos_client.close()
    mongos_host = await get_address_of_unit(ops_test, unit_id=0)
    test_user_uri = f"mongodb://{TEST_USER_NAME}:{TEST_USER_PWD}@{mongos_host}:{MONGOS_PORT}"
    mongos_running = await check_mongos(
        ops_test,
        unit_id=0,
        app_name=MONGOS_APP_NAME,
        auth=True,
        uri=test_user_uri,
    )
    assert mongos_running, "User created is not accessible."


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_can_scale(ops_test: OpsTest) -> None:
    """Tests that mongos powers down when no config server is accessible."""
    await ops_test.model.applications[MONGOS_APP_NAME].scale(2)

    await ops_test.model.wait_for_idle(
        apps=[MONGOS_APP_NAME],
        status="active",
        timeout=1000,
    )

    for unit_id in range(0, len(ops_test.model.applications[MONGOS_APP_NAME].units)):
        mongos_running = await check_mongos(ops_test, unit_id=unit_id, auth=True)
        assert mongos_running, "Mongos is not currently running."
