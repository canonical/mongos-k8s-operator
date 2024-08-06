#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from pathlib import Path

import pytest
import yaml
from pytest_operator.plugin import OpsTest

from .helpers import (
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
