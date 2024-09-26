#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path
import shutil
import zipfile

import pytest
import pytest_asyncio
from pytest_operator.plugin import OpsTest

from ..helpers import (
    MONGOS_APP_NAME,
    build_cluster,
    deploy_cluster_components,
)


@pytest_asyncio.fixture
async def upgrade_charm(ops_test: OpsTest, tmp_path: Path):
    local_charm: Path = await ops_test.build_charm(".")
    righty_charm = tmp_path / "righty_charm.charm"
    shutil.copy(local_charm, righty_charm)
    workload_version = Path("workload_version").read_text().strip()

    [major, minor, patch] = workload_version.split(".")

    with zipfile.ZipFile(righty_charm, mode="a") as charm_zip:
        charm_zip.writestr(
            "workload_version", f"{major}.{int(minor)+1}.{patch}+testupgrade"
        )

    yield righty_charm


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy a sharded cluster."""
    await deploy_cluster_components(ops_test, n_units=3)
    await build_cluster(ops_test)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_successful_local_upgrade(ops_test: OpsTest, upgrade_charm: Path) -> None:
    await ops_test.model.applications[MONGOS_APP_NAME].refresh(path=upgrade_charm)
    await ops_test.model.wait_for_idle(
        apps=[MONGOS_APP_NAME], status="active", timeout=1000, idle_period=120
    )
