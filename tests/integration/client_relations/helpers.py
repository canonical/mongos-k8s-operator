#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from typing import Tuple
import logging

from pathlib import Path
import yaml

from pytest_operator.plugin import OpsTest
from ..helpers import get_application_relation_data, get_secret_data

logger = logging.getLogger(__name__)


MONGOS_APP_NAME = "mongos-k8s"
MONGODB_CHARM_NAME = "mongodb-k8s"
CONFIG_SERVER_APP_NAME = "config-server"
SHARD_APP_NAME = "shard0"
MONGOS_PORT = 27018
SHARD_REL_NAME = "sharding"
CONFIG_SERVER_REL_NAME = "config-server"
CLUSTER_REL_NAME = "cluster"

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())


async def get_mongos_user_password(
    ops_test: OpsTest, app_name=MONGOS_APP_NAME, relation_name="cluster"
) -> Tuple[str, str]:
    # TODO once DPE:5215 is fixed retrieve via secret
    secret_uri = await get_application_relation_data(
        ops_test, app_name, relation_name=relation_name, key="secret-user"
    )

    secret_data = await get_secret_data(ops_test, secret_uri)
    return secret_data.get("username"), secret_data.get("password")


async def get_client_connection_string(
    ops_test: OpsTest, app_name=MONGOS_APP_NAME, relation_name="cluster"
) -> Tuple[str, str]:
    secret_uri = await get_application_relation_data(
        ops_test, app_name, relation_name=relation_name, key="secret-user"
    )

    secret_data = await get_secret_data(ops_test, secret_uri)
    return secret_data.get("uris")


def is_relation_joined(ops_test: OpsTest, endpoint_one: str, endpoint_two: str) -> bool:
    """Check if a relation is joined.

    Args:
        ops_test: The ops test object passed into every test case
        endpoint_one: The first endpoint of the relation
        endpoint_two: The second endpoint of the relation
    """
    for rel in ops_test.model.relations:
        endpoints = [endpoint.name for endpoint in rel.endpoints]
        if endpoint_one in endpoints and endpoint_two in endpoints:
            return True
    return False
