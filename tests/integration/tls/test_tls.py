#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from pytest_operator.plugin import OpsTest
from ..helpers import deploy_cluster_components, build_cluster, wait_for_mongos_units_blocked
from .helpers import (
    check_mongos_tls_enabled,
    check_mongos_tls_disabled,
    toggle_tls_mongos,
    EXTERNAL_CERT_PATH,
    INTERNAL_CERT_PATH,
    get_file_contents,
    check_certs_correctly_distributed,
    time_file_created,
    time_process_started,
    deploy_tls,
    integrate_mongos_with_tls,
    integrate_cluster_with_tls,
    MONGOS_APP_NAME,
    CERT_REL_NAME,
    CERTS_APP_NAME,
    CONFIG_SERVER_APP_NAME,
    SHARD_APP_NAME,
    CLUSTER_COMPONENTS,
    rotate_and_verify_certs,
)


MONGOS_SERVICE = "mongos.service"

MONGOS_SERVICE = "snap.charmed-mongodb.mongos.service"
APPLICATION_APP_NAME = "application"
MONGODB_CHARM_NAME = "mongodb"
SHARD_APP_NAME = "shard"
SHARD_REL_NAME = "sharding"
CLUSTER_REL_NAME = "cluster"
CONFIG_SERVER_REL_NAME = "config-server"
DIFFERENT_CERTS_APP_NAME = "self-signed-certificates-separate"
TIMEOUT = 15 * 60


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest) -> None:
    """Build and deploy a sharded cluster."""
    await deploy_cluster_components(ops_test)
    await build_cluster(ops_test)
    await deploy_tls(ops_test)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_tls_enabled(ops_test: OpsTest) -> None:
    """Tests that mongos charm can enable TLS."""
    await integrate_mongos_with_tls(ops_test)

    await wait_for_mongos_units_blocked(
        ops_test,
        MONGOS_APP_NAME,
        status="mongos has TLS enabled, but config-server does not.",
        timeout=TIMEOUT,
    )

    await integrate_cluster_with_tls(ops_test)

    await check_mongos_tls_enabled(ops_test)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_rotate_certs(ops_test: OpsTest) -> None:
    await rotate_and_verify_certs(ops_test, MONGOS_APP_NAME)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_tls_disabled(ops_test: OpsTest) -> None:
    """Tests that mongos charm can disable TLS."""
    await toggle_tls_mongos(ops_test, enable=False)
    await check_mongos_tls_disabled(ops_test)

    await ops_test.model.wait_for_idle(
        apps=[MONGOS_APP_NAME],
        idle_period=60,
        timeout=TIMEOUT,
        raise_on_blocked=False,
    )

    for mongos_unit in ops_test.model.applications[MONGOS_APP_NAME].units:
        assert (
            mongos_unit.workload_status_message == "mongos requires TLS to be enabled."
        ), "mongos fails to report TLS inconsistencies."


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_tls_reenabled(ops_test: OpsTest) -> None:
    """Test that mongos can enable TLS after being integrated to cluster ."""
    await toggle_tls_mongos(ops_test, enable=True)
    await check_mongos_tls_enabled(ops_test)


# @pytest.mark.group(1)
# @pytest.mark.abort_on_fail
# async def test_mongos_tls_ca_mismatch(ops_test: OpsTest) -> None:
#     """Tests that mongos charm can disable TLS."""
#     await toggle_tls_mongos(ops_test, enable=False)
#     await ops_test.model.deploy(
#         CERTS_APP_NAME, application_name=DIFFERENT_CERTS_APP_NAME, channel="stable"
#     )
#     await ops_test.model.wait_for_idle(
#         apps=[DIFFERENT_CERTS_APP_NAME],
#         idle_period=10,
#         raise_on_blocked=False,
#         status="active",
#         timeout=TIMEOUT,
#     )

#     await toggle_tls_mongos(ops_test, enable=True, certs_app_name=DIFFERENT_CERTS_APP_NAME)

#     await ops_test.model.wait_for_idle(
#         apps=[MONGOS_APP_NAME],
#         idle_period=20,
#         raise_on_blocked=False,
#         timeout=TIMEOUT,
#     )

#     mongos_unit = ops_test.model.applications[MONGOS_APP_NAME].units[0]
#     assert (
#         mongos_unit.workload_status_message == "mongos CA and Config-Server CA don't match."
#     ), "mongos fails to report mismatch in CA."
