#!/usr/bin/env python3
"""Charm code for `mongos` daemon."""

from ops.main import main

from single_kernel_mongo.abstract_charm import AbstractMongoCharm
from single_kernel_mongo.config.literals import Substrates
from single_kernel_mongo.config.relations import PeerRelationNames
from single_kernel_mongo.core.structured_config import MongosCharmConfig
from single_kernel_mongo.managers.mongos_operator import MongosOperator


# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
class MongosK8sCharm(AbstractMongoCharm[MongosCharmConfig, MongosOperator]):
    """Charm the service."""

    config_type = MongosCharmConfig
    operator_type = MongosOperator
    substrate = Substrates.K8S
    peer_rel_name = PeerRelationNames.ROUTER_PEERS
    name = "mongos"


if __name__ == "__main__":
    main(MongosK8sCharm)
