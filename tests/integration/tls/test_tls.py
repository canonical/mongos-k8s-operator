#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from pathlib import Path
import pytest
from pytest_operator.plugin import OpsTest
from ..helpers import (
    deploy_cluster_components,
    build_cluster,
    wait_for_mongos_units_blocked,
)
from .helpers import (
    check_mongos_tls_enabled,
    check_mongos_tls_disabled,
    toggle_tls_mongos,
    deploy_tls,
    integrate_mongos_with_tls,
    integrate_cluster_with_tls,
    MONGOS_APP_NAME,
    CERTS_APP_NAME,
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
async def test_mongos_rotate_certs(ops_test: OpsTest, tmp_path: Path) -> None:
    await rotate_and_verify_certs(ops_test, MONGOS_APP_NAME, tmpdir=tmp_path)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_tls_disabled(ops_test: OpsTest) -> None:
    """Tests that mongos charm can disable TLS."""
    await toggle_tls_mongos(ops_test, enable=False)
    await check_mongos_tls_disabled(ops_test)

    await wait_for_mongos_units_blocked(
        ops_test,
        MONGOS_APP_NAME,
        status="mongos requires TLS to be enabled.",
        timeout=300,
    )


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_tls_reenabled(ops_test: OpsTest) -> None:
    """Test that mongos can enable TLS after being integrated to cluster ."""
    await toggle_tls_mongos(ops_test, enable=True)
    await check_mongos_tls_enabled(ops_test)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_mongos_tls_ca_mismatch(ops_test: OpsTest) -> None:
    """Tests that mongos charm can disable TLS."""
    await toggle_tls_mongos(ops_test, enable=False)
    await ops_test.model.deploy(
        CERTS_APP_NAME, application_name=DIFFERENT_CERTS_APP_NAME, channel="stable"
    )
    await ops_test.model.wait_for_idle(
        apps=[DIFFERENT_CERTS_APP_NAME],
        idle_period=10,
        raise_on_blocked=False,
        status="active",
        timeout=TIMEOUT,
    )

    await toggle_tls_mongos(
        ops_test, enable=True, certs_app_name=DIFFERENT_CERTS_APP_NAME
    )
    await wait_for_mongos_units_blocked(
        ops_test,
        MONGOS_APP_NAME,
        status="mongos CA and Config-Server CA don't match.",
        timeout=300,
    )