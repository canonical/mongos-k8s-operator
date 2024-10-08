from collections.abc import AsyncGenerator
import logging
import shutil
import pytest_asyncio
import pytest
from pathlib import Path
import time
import zipfile

from pytest_operator.plugin import OpsTest
import tenacity

from ..helpers import (
    MONGOS_APP_NAME,
    build_cluster,
    deploy_cluster_components,
    get_juju_status,
)

logger = logging.getLogger(__name__)
UPGRADE_TIMEOUT = 15 * 60


@pytest_asyncio.fixture
async def local_charm(ops_test: OpsTest) -> AsyncGenerator[Path]:
    new_charm = await ops_test.build_charm(".")
    yield new_charm


@pytest_asyncio.fixture
def faulty_upgrade_charm(local_charm, tmp_path: Path):
    fault_charm = tmp_path / "fault_charm.charm"
    shutil.copy(local_charm, fault_charm)
    workload_version = Path("workload_version").read_text().strip()

    [major, minor, patch] = workload_version.split(".")

    with zipfile.ZipFile(fault_charm, mode="a") as charm_zip:
        charm_zip.writestr(
            "workload_version", f"{int(major) -1}.{minor}.{patch}+testrollback"
        )

    yield fault_charm


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy a sharded cluster."""
    await deploy_cluster_components(ops_test, n_units=3)
    await build_cluster(ops_test)


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_rollback(ops_test: OpsTest, local_charm, faulty_upgrade_charm) -> None:
    mongos_application = ops_test.model.applications[MONGOS_APP_NAME]
    await mongos_application.refresh(path=faulty_upgrade_charm)
    logger.info("Wait for upgrade to fail")

    for attempt in tenacity.Retrying(
        reraise=True,
        stop=tenacity.stop_after_delay(UPGRADE_TIMEOUT),
        wait=tenacity.wait_fixed(10),
    ):
        with attempt:
            assert "Refresh incompatible" in get_juju_status(
                ops_test.model.name, MONGOS_APP_NAME
            ), "Not indicating charm incompatible"

    logger.info("Re-refresh the charm")
    await mongos_application.refresh(path=local_charm)
    # sleep to ensure that active status from before re-refresh does not affect below check
    time.sleep(15)
    await ops_test.model.block_until(
        lambda: all(
            unit.workload_status == "active" for unit in mongos_application.units
        )
        and all(unit.agent_status == "idle" for unit in mongos_application.units)
    )

    logger.info("Wait for the charm to be rolled back")
    await ops_test.model.wait_for_idle(
        apps=[MONGOS_APP_NAME],
        status="active",
        timeout=1000,
        idle_period=30,
    )
