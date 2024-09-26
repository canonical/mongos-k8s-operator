#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import pytest
from pytest_operator.plugin import OpsTest

from ..helpers import (
    MONGOS_APP_NAME,
    build_cluster,
    deploy_cluster_components,
)


@pytest.mark.skip(reason="Enable this when charm is on charmhub")
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy a sharded cluster."""
    await deploy_cluster_components(ops_test, channel="6/edge")
    await build_cluster(ops_test)


@pytest.mark.skip(reason="Enable this when charm is on charmhub")
@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_successful_upgrade(ops_test: OpsTest) -> None:
    new_charm: Path = await ops_test.build_charm(".")
    await ops_test.model.applications[MONGOS_APP_NAME].refresh(path=new_charm)
    await ops_test.model.wait_for_idle(
        apps=[MONGOS_APP_NAME], status="active", timeout=1000, idle_period=120
    )
