"""Configuration for Mongos Charm."""
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from typing import Literal
from ops.model import BlockedStatus


class Config:
    """Configuration for MongoDB Charm."""

    MONGOS_PORT = 27018
    MONGODB_PORT = 27017
    SUBSTRATE = "k8s"
    CONTAINER_NAME = "mongos"
    USER_ROLE_CREATE_USERS = "admin"
    SERVICE_NAME = "mongos"  # this must match the name of the service in the ROCK
    MONGOD_CONF_DIR = "/etc/mongod"
    UNIX_USER = "mongodb"
    UNIX_GROUP = "mongodb"
    LICENSE_PATH = "/licenses/LICENSE"
    DATA_DIR = "/var/lib/mongodb"

    class ExternalConnections:
        """External Connections related config for MongoDB Charm."""

        NONE = "none"
        EXTERNAL_NODEPORT = "nodeport"
        VALID_EXTERNAL_CONFIG = [NONE, EXTERNAL_NODEPORT]

    class Relations:
        """Relations related config for MongoDB Charm."""

        APP_SCOPE = "app"
        UNIT_SCOPE = "unit"
        PEERS = "router-peers"
        CLUSTER_RELATIONS_NAME = "cluster"
        CLIENT_RELATIONS_NAME = "mongos_proxy"
        Scopes = Literal[APP_SCOPE, UNIT_SCOPE]

    class TLS:
        """TLS related config for MongoDB Charm."""

        KEY_FILE_NAME = "keyFile"
        TLS_PEER_RELATION = "certificates"
        SECRET_KEY_LABEL = "key-secret"

        EXT_PEM_FILE = "external-cert.pem"
        EXT_CA_FILE = "external-ca.crt"
        INT_PEM_FILE = "internal-cert.pem"
        INT_CA_FILE = "internal-ca.crt"
        SECRET_CA_LABEL = "ca-secret"
        SECRET_CERT_LABEL = "cert-secret"
        SECRET_CSR_LABEL = "csr-secret"
        SECRET_CHAIN_LABEL = "chain-secret"

    class Secrets:
        """Secrets related constants."""

        SECRET_LABEL = "secret"
        SECRET_CACHE_LABEL = "cache"
        SECRET_KEYFILE_NAME = "keyfile"
        SECRET_INTERNAL_LABEL = "internal-secret"
        USERNAME = "username"
        PASSWORD = "password"
        SECRET_DELETED_LABEL = "None"
        MAX_PASSWORD_LENGTH = 4096

    class Status:
        """Status related constants.

        TODO: move all status messages here.
        """

        STATUS_READY_FOR_UPGRADE = "status-shows-ready-for-upgrade"

        # TODO Future PR add more status messages here as constants
        UNHEALTHY_UPGRADE = BlockedStatus("Unhealthy after upgrade.")
        INVALID_EXTERNAL_CONFIG = BlockedStatus(
            "Config option for expose-external not valid."
        )

    class Substrate:
        """Substrate related constants."""

        VM = "vm"
        K8S = "k8s"

    class Role:
        """Role config names for MongoDB Charm."""

        CONFIG_SERVER = "config-server"
        REPLICATION = "replication"
        SHARD = "shard"
        MONGOS = "mongos"

    @staticmethod
    def get_license_path(license_name: str) -> str:
        """Return the path to the license file."""
        return f"{Config.LICENSE_PATH}-{license_name}"
