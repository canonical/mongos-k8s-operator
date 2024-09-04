#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.


import pytest
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed

from ..helpers import (
    deploy_cluster_components,
    build_cluster,
    MONGOS_APP_NAME,
    wait_for_mongos_units_blocked,
)

from .helpers import (
    deploy_client_app,
    integrate_client_app,
    assert_all_unit_node_ports_available,
    assert_all_unit_node_ports_are_unavailable,
    get_port_from_node_port,
    is_external_mongos_client_reachable,
    DATA_INTEGRATOR_APP_NAME,
    APPLICATION_APP_NAME,
    get_client_connection_string,
    get_public_k8s_ip,
    get_k8s_local_mongodb_hosts,
    assert_app_uri_matches_external_setting,
)


TEST_USER_NAME = "TestUserName1"
TEST_USER_PWD = "Test123"
TEST_DB_NAME = "my-test-db"


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_build_and_deploy(ops_test: OpsTest):
#     """Build and deploy a sharded cluster."""
#     # await deploy_cluster_components(ops_test)
#     await build_cluster(ops_test)

#     await deploy_client_app(ops_test, external=False)
#     await integrate_client_app(ops_test, client_app_name=APPLICATION_APP_NAME)

#     await deploy_client_app(ops_test, external=True)
#     await ops_test.model.applications[DATA_INTEGRATOR_APP_NAME].set_config(
#         {"database-name": "test-database"}
#     )
#     await integrate_client_app(ops_test, client_app_name=DATA_INTEGRATOR_APP_NAME)


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_mongos_external_connections(ops_test: OpsTest) -> None:
#     """Tests that mongos is accessible externally."""
#     configuration_parameters = {"expose-external": "nodeport"}

#     # apply new configuration options
#     await ops_test.model.applications[MONGOS_APP_NAME].set_config(configuration_parameters)
#     await ops_test.model.wait_for_idle(apps=[MONGOS_APP_NAME], idle_period=15)

#     # verify each unit has a node port available
#     await assert_all_unit_node_ports_available(ops_test)


@pytest.mark.group(1)
# @pytest.mark.skip("Add in once DPE-5314 is addressed.")
@pytest.mark.abort_on_fail
async def test_mongos_external_connections_scale(ops_test: OpsTest) -> None:
    """Tests that new mongos units are accessible externally."""
    await ops_test.model.applications[MONGOS_APP_NAME].scale(2)
    await ops_test.model.wait_for_idle(apps=[MONGOS_APP_NAME], status="active", idle_period=30)
    await assert_all_unit_node_ports_available(ops_test)


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_mongos_bad_configuration(ops_test: OpsTest) -> None:
#     """Tests that mongos is accessible externally."""
#     configuration_parameters = {"expose-external": "nonsensical-setting"}

#     # apply new configuration options
#     await ops_test.model.applications[MONGOS_APP_NAME].set_config(configuration_parameters)

#     # verify that Charmed Mongos is blocked and reports incorrect credentials
#     await wait_for_mongos_units_blocked(
#         ops_test,
#         MONGOS_APP_NAME,
#         status="Config option for expose-external not valid.",
#         timeout=300,
#     )

#     # verify new-configuration didn't break old configuration
#     await assert_all_unit_node_ports_available(ops_test)

#     # reset config for other tests
#     configuration_parameters = {"expose-external": "nodeport"}
#     await ops_test.model.applications[MONGOS_APP_NAME].set_config(configuration_parameters)
#     await ops_test.model.wait_for_idle(apps=[MONGOS_APP_NAME], status="active", idle_period=15)


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_all_clients_use_nodeport(ops_test: OpsTest) -> None:
#     """Test that all clients use nodeport."""
#     await assert_app_uri_matches_external_setting(
#         ops_test, app_name=DATA_INTEGRATOR_APP_NAME, rel_name="mongodb", external=True
#     )
#     await assert_app_uri_matches_external_setting(
#         ops_test, app_name=APPLICATION_APP_NAME, rel_name="mongos", external=True
#     )


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_mongos_disable_external_connections(ops_test: OpsTest) -> None:
#     """Tests that mongos can disable external connections."""
#     # get exposed node port before toggling off exposure
#     exposed_node_port = get_port_from_node_port(
#         ops_test, node_port_name=f"{MONGOS_APP_NAME}-0-external"
#     )

#     configuration_parameters = {"expose-external": "none"}

#     # apply new configuration options
#     await ops_test.model.applications[MONGOS_APP_NAME].set_config(configuration_parameters)
#     await ops_test.model.wait_for_idle(
#         apps=[MONGOS_APP_NAME, DATA_INTEGRATOR_APP_NAME], idle_period=15
#     )

#     # verify each unit has a node port available
#     await assert_all_unit_node_ports_are_unavailable(ops_test)

#     assert not await is_external_mongos_client_reachable(ops_test, exposed_node_port)

#     await assert_app_uri_matches_external_setting(
#         ops_test, app_name=DATA_INTEGRATOR_APP_NAME, rel_name="mongodb", external=False
#     )
#     await assert_app_uri_matches_external_setting(
#         ops_test, app_name=APPLICATION_APP_NAME, rel_name="mongos", external=False
#     )
