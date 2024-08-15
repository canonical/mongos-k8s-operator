#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from .helpers import (
    assert_node_port_available,
    wait_for_mongos_units_blocked,
    MONGOS_APP_NAME,
)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


@pytest.mark.group(1)
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """TODO: Build the charm-under-test and deploy it together with related charms.

    Assert on the unit status before any relations/configurations take place.
    """
    charm = await ops_test.build_charm(".")
    resources = {
        "mongodb-image": METADATA["resources"]["mongodb-image"]["upstream-source"]
    }
    await ops_test.model.deploy(
        charm,
        resources=resources,
        application_name=MONGOS_APP_NAME,
        series="jammy",
        num_units=2,
    )

    await ops_test.model.wait_for_idle(
        apps=[MONGOS_APP_NAME], timeout=1000, idle_period=30
    )


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
async def test_mongos_external_connections(ops_test: OpsTest) -> None:
    """Tests that mongos is accessible externally."""
    for unit_id in range(len(ops_test.model.applications[MONGOS_APP_NAME].units)):
        assert_node_port_available(
            ops_test, node_port_name=f"{MONGOS_APP_NAME}-{unit_id}-external"
        )

        # TODO add this in once DPE-5040 / PR #20 merges
        # exposed_node_port = get_port_from_node_port(ops_test, node_port_name="mongos-k8s-nodeport")
        # public_k8s_ip = get_public_k8s_ip()
        # username, password = await get_mongos_user_password(ops_test, MONGOS_APP_NAME)
        # external_mongos_client = MongoClient(
        #     f"mongodb://{username}:{password}@{public_k8s_ip}:{exposed_node_port}"
        # )
        # external_mongos_client.admin.command("usersInfo")
        # external_mongos_client.close()
