#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

import json
import subprocess
import logging

from typing import Any, Dict, List, Optional, Tuple

from pathlib import Path
import yaml
from pymongo import MongoClient

from dateutil.parser import parse
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, stop_after_delay, wait_fixed
from tenacity import (
    RetryError,
)

logger = logging.getLogger(__name__)

PORT_MAPPING_INDEX = 4


def get_port_from_node_port(ops_test: OpsTest, node_port_name: str) -> None:
    node_port_cmd = (
        f"kubectl get svc  -n  {ops_test.model.name} |  grep NodePort | grep {node_port_name}"
    )
    result = subprocess.run(node_port_cmd, shell=True, capture_output=True, text=True)
    if result.returncode:
        logger.info("was not able to find nodeport")
        assert False, f"Command: {node_port_cmd} to find node port failed."

    assert (
        len(result.stdout.splitlines()) > 0
    ), "No port information available for expected service"

    # port information is available at PORT_MAPPING_INDEX
    port_mapping = result.stdout.split()[PORT_MAPPING_INDEX]

    # port information is of the form 27018:30259/TCP
    return port_mapping.split(":")[1].split("/")[0]


def assert_node_port_available(ops_test: OpsTest, node_port_name: str) -> None:
    assert get_port_from_node_port(
        ops_test, node_port_name
    ), "No port information for expected service"


def get_public_k8s_ip() -> str:
    result = subprocess.run("kubectl get nodes", shell=True, capture_output=True, text=True)

    if result.returncode:
        logger.info("failed to retrieve public facing k8s IP")
        assert False, "failed to retrieve public facing k8s IP"

    # port information is the first item of the last line
    port_mapping = result.stdout.splitlines()[-1].split()[0]

    # port mapping is of the form ip-172-31-18-133
    return port_mapping.split("ip-")[1].replace("-", ".")
