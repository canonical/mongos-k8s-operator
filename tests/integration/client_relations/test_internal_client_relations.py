#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.


import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    build_cluster,
    MONGOS_APP_NAME,
    deploy_cluster_components,
    get_address_of_unit,
    check_mongos,
    get_direct_mongos_client,
    MONGOS_PORT,
)
from .helpers import (
    deploy_client_app,
    integrate_client_app,
    is_relation_joined,
    get_client_connection_string,
    get_mongos_user_password,
    APPLICATION_APP_NAME,
)

CLIENT_RELATION_NAME = "mongos"
MONGOS_RELATION_NAME = "mongos_proxy"

TEST_USER_NAME = "TestUserName1"
TEST_USER_PWD = "Test123"
TEST_DB_NAME = "my-test-db"


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy a sharded cluster."""

    await deploy_client_app(ops_test, external=False)
    await build_cluster(ops_test)
    await deploy_cluster_components(ops_test)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_integrate_with_internal_client(ops_test: OpsTest) -> None:
    """Tests that when a client is integrated with mongos a user it receives connection info."""
    await integrate_client_app(ops_test, client_app_name=APPLICATION_APP_NAME)

    await ops_test.model.block_until(
        lambda: is_relation_joined(
            ops_test,
            CLIENT_RELATION_NAME,
            MONGOS_RELATION_NAME,
        )
        is True,
        timeout=600,
    )

    connection_string = await get_client_connection_string(
        ops_test, APPLICATION_APP_NAME, CLIENT_RELATION_NAME
    )
    assert connection_string, "Connection string  not provided to client."

    username, password = await get_mongos_user_password(
        ops_test, app_name=APPLICATION_APP_NAME, relation_name="mongos"
    )
    assert username, "Username not provided to client."
    assert password, "Password not provided to client."


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_user_can_connect(ops_test: OpsTest) -> None:
    """Tests that the user created by mongos can connect with auth."""
    username, password = await get_mongos_user_password(
        ops_test, app_name=APPLICATION_APP_NAME, relation_name="mongos"
    )
    assert username, "Username not provided to client"
    assert password, "Password not provided to client"

    mongos_host = await get_address_of_unit(ops_test, unit_id=0)
    client_user_uri = f"mongodb://{username}:{password}@{mongos_host}:{MONGOS_PORT}"
    mongos_can_connect_with_auth = await check_mongos(ops_test, uri=client_user_uri)
    assert mongos_can_connect_with_auth, "User created cannot connect with auth."


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_user_with_extra_roles(ops_test: OpsTest) -> None:
    """Tests that the user created by mongos router has the permissions it asks for."""
    username, password = await get_mongos_user_password(
        ops_test, app_name=APPLICATION_APP_NAME, relation_name="mongos"
    )
    mongos_host = await get_address_of_unit(ops_test, unit_id=0)
    client_user_uri = f"mongodb://{username}:{password}@{mongos_host}:{MONGOS_PORT}"

    mongos_client = await get_direct_mongos_client(ops_test, uri=client_user_uri)
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
    test_user_accessible = await check_mongos(ops_test, uri=test_user_uri)
    assert test_user_accessible, "User created is not accessible."


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_removed_relation_no_longer_has_access(ops_test: OpsTest):
    """Verify removed applications no longer have access to the database."""
    # before removing relation we need its authorisation via connection string
    username, password = await get_mongos_user_password(
        ops_test, app_name=APPLICATION_APP_NAME, relation_name="mongos"
    )
    mongos_host = await get_address_of_unit(ops_test, unit_id=0)
    client_user_uri = f"mongodb://{username}:{password}@{mongos_host}:{MONGOS_PORT}"

    await ops_test.model.applications[MONGOS_APP_NAME].remove_relation(
        f"{APPLICATION_APP_NAME}:{CLIENT_RELATION_NAME}", f"{MONGOS_APP_NAME}"
    )
    await ops_test.model.wait_for_idle(apps=[MONGOS_APP_NAME], status="active", idle_period=20)

    mongos_can_connect_with_auth = await check_mongos(ops_test, uri=client_user_uri)

    assert not mongos_can_connect_with_auth, "Client can still connect after relation broken."
