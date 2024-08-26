#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.


import pytest
from pytest_operator.plugin import OpsTest


from .helpers import assert_all_unit_node_ports_available


TEST_USER_NAME = "TestUserName1"
TEST_USER_PWD = "Test123"
TEST_DB_NAME = "my-test-db"


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_build_and_deploy(ops_test: OpsTest):
#     """Build and deploy a sharded cluster."""
#     await deploy_cluster_components(ops_test)
#     await build_cluster(ops_test)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_external_connections(ops_test: OpsTest) -> None:
    """Tests that mongos is accessible externally."""
    # configuration_parameters = {"expose-external": "nodeport"}

    # # apply new configuration options
    # await ops_test.model.applications[MONGOS_APP_NAME].set_config(configuration_parameters)
    # await ops_test.model.wait_for_idle(apps=[MONGOS_APP_NAME], idle_period=15)

    # # verify each unit has a node port available
    await assert_all_unit_node_ports_available(ops_test)


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_mongos_external_connections_scale(ops_test: OpsTest) -> None:
#     """Tests that new mongos units are accessible externally."""
#     await ops_test.model.applications[MONGOS_APP_NAME].scale(2)
#     await ops_test.model.wait_for_idle(apps=[MONGOS_APP_NAME], idle_period=15)

#     # verify each unit has a node port available
#     await assert_all_unit_node_ports_available(ops_test)


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
#         status="Missing relation to config-server.",
#         timeout=300,
#     )

#     # verify new-configuration didn't break old configuration
#     await assert_all_unit_node_ports_available(ops_test)


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_turn_off_nodeport(ops_test: OpsTest) -> None:
#     """TODO Future PR, test that when the user toggles nodeport to none, it is no longer exposed."""


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_external_clients_use_nodeport(ops_test: OpsTest) -> None:
#     """TODO Future PR, test that external clients use nodeport."""


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_internal_clients_use_K8s(ops_test: OpsTest) -> None:
#     """TODO Future PR, test that external clients use K8s even when nodeport is available."""
