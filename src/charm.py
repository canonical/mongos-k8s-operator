#!/usr/bin/env python3
"""Charm code for `mongos` daemon."""

# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
import json

from exceptions import MissingSecretError

from ops.pebble import PathError, ProtocolError, Layer, APIError


from typing import Set, Optional, Dict

from charms.mongodb.v0.config_server_interface import ClusterRequirer

from charms.mongos.v0.set_status import MongosStatusHandler

from charms.mongodb.v0.mongodb_tls import MongoDBTLS
from charms.mongodb.v0.mongodb_secrets import SecretCache
from charms.mongodb.v0.mongodb_secrets import generate_secret_label
from charms.mongodb.v1.mongos import MongosConfiguration, MongosConnection
from charms.mongodb.v1.users import (
    MongoDBUser,
)

from charms.mongodb.v1.helpers import get_mongos_args

from config import Config

import ops
from ops.model import BlockedStatus, Container, Relation, ActiveStatus, Unit
from ops.charm import StartEvent, RelationDepartedEvent

import logging


logger = logging.getLogger(__name__)

APP_SCOPE = Config.Relations.APP_SCOPE
UNIT_SCOPE = Config.Relations.UNIT_SCOPE
ROOT_USER_GID = 0
MONGO_USER = "snap_daemon"
ENV_VAR_PATH = "/etc/environment"
MONGOS_VAR = "MONGOS_ARGS"
CONFIG_ARG = "--configdb"
USER_ROLES_TAG = "extra-user-roles"
DATABASE_TAG = "database"
EXTERNAL_CONNECTIVITY_TAG = "external-connectivity"


class MissingConfigServerError(Exception):
    """Raised when mongos expects to be connected to a config-server but is not."""


class MongosCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.mongos_pebble_ready, self._on_mongos_pebble_ready)
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.update_status, self._on_update_status)
        self.tls = MongoDBTLS(self, Config.Relations.PEERS, substrate=Config.K8S_SUBSTRATE)

        self.role = Config.Role.MONGOS
        self.secrets = SecretCache(self)
        self.status = MongosStatusHandler(self)
        self.cluster = ClusterRequirer(self, substrate=Config.K8S_SUBSTRATE)

    # BEGIN: hook functions
    def _on_mongos_pebble_ready(self, event) -> None:
        """Configure MongoDB pebble layer specification."""
        if not self.is_integrated_to_config_server():
            logger.info(
                "mongos service not starting. Cannot start until application is integrated to a config-server."
            )
            return

        # Get a reference the container attribute
        container = self.unit.get_container(Config.CONTAINER_NAME)
        if not container.can_connect():
            logger.debug("mongos container is not ready yet.")
            event.defer()
            return

        try:
            # mongos needs keyFile and TLS certificates on filesystem
            self._push_keyfile_to_workload(container)
            self._pull_licenses(container)
            self._set_data_dir_permissions(container)

        except (PathError, ProtocolError, MissingSecretError) as e:
            logger.error("Cannot initialize workload: %r", e)
            event.defer()
            return

        # Add initial Pebble config layer using the Pebble API
        container.add_layer(Config.CONTAINER_NAME, self._mongos_layer, combine=True)

        # Restart changed services and start startup-enabled services.
        container.replan()

    def _on_start(self, event: StartEvent) -> None:
        """Handle the start event."""
        # start hooks are fired before relation hooks and `mongos` requires a config-server in
        # order to start. Wait to receive config-server info from the relation event before
        # starting `mongos` daemon
        self.status.set_and_share_status(BlockedStatus("Missing relation to config-server."))

    def _on_update_status(self, _):
        """Handle the update status event"""
        if self.unit.status == Config.Status.UNHEALTHY_UPGRADE:
            return

        if not self.is_integrated_to_config_server():
            logger.info(
                "Missing integration to config-server. mongos cannot run unless connected to config-server."
            )
            self.status.set_and_share_status(BlockedStatus("Missing relation to config-server."))
            return

        self.status.set_and_share_status(ActiveStatus())

    # END: hook functions

    # BEGIN: helper functions
    def get_keyfile_contents(self) -> str:
        """Retrieves the contents of the keyfile on host machine."""
        # wait for keyFile to be created by leader unit
        if not self.get_secret(APP_SCOPE, Config.Secrets.SECRET_KEYFILE_NAME):
            logger.debug("waiting to recieve keyfile contents from config-server.")

        try:
            container = self.unit.get_container(Config.CONTAINER_NAME)
            key = container.pull(f"{Config.MONGOD_CONF_DIR}/{Config.TLS.KEY_FILE_NAME}")
            return key.read()
        except PathError:
            logger.info("no keyfile present")
            return

    def is_integrated_to_config_server(self) -> bool:
        """Returns True if the mongos application is integrated to a config-server."""
        return self.model.relations[Config.Relations.CLUSTER_RELATIONS_NAME] is not None

    def _get_mongos_config_for_user(
        self, user: MongoDBUser, hosts: Set[str]
    ) -> MongosConfiguration:
        return MongosConfiguration(
            database=user.get_database_name(),
            username=user.get_username(),
            password=self.get_secret(APP_SCOPE, user.get_password_key_name()),
            hosts=hosts,
            port=Config.MONGOS_PORT,
            roles=user.get_roles(),
            tls_external=None,  # Future PR will support TLS
            tls_internal=None,  # Future PR will support TLS
        )

    def get_secret(self, scope: str, key: str) -> Optional[str]:
        """Get secret from the secret storage."""
        label = generate_secret_label(self, scope)
        secret = self.secrets.get(label)
        if not secret:
            return

        value = secret.get_content().get(key)
        if value != Config.Secrets.SECRET_DELETED_LABEL:
            return value

    def set_secret(self, scope: str, key: str, value: Optional[str]) -> Optional[str]:
        """Set secret in the secret storage.

        Juju versions > 3.0 use `juju secrets`, this function first checks
          which secret store is being used before setting the secret.
        """
        if not value:
            return self.remove_secret(scope, key)

        label = generate_secret_label(self, scope)
        secret = self.secrets.get(label)
        if not secret:
            self.secrets.add(label, {key: value}, scope)
        else:
            content = secret.get_content()
            content.update({key: value})
            secret.set_content(content)
        return label

    def remove_secret(self, scope, key) -> None:
        """Removing a secret."""
        label = generate_secret_label(self, scope)
        secret = self.secrets.get(label)

        if not secret:
            return

        content = secret.get_content()

        if not content.get(key) or content[key] == Config.Secrets.SECRET_DELETED_LABEL:
            logger.error(f"Non-existing secret {scope}:{key} was attempted to be removed.")
            return

        content[key] = Config.Secrets.SECRET_DELETED_LABEL
        secret.set_content(content)

    def stop_mongos_service(self):
        """Stop mongos service."""
        container = self.unit.get_container(Config.CONTAINER_NAME)
        container.stop(Config.SERVICE_NAME)

    def restart_charm_services(self):
        """Restart mongos service."""
        container = self.unit.get_container(Config.CONTAINER_NAME)
        try:
            container.stop(Config.SERVICE_NAME)
        except APIError:
            # stopping a service that is not running results in an APIError which can be ignored.
            pass

        container.add_layer(Config.CONTAINER_NAME, self._mongos_layer, combine=True)
        container.replan()

    def set_database(self, database: str) -> None:
        """Updates the database requested for the mongos user."""
        self.app_peer_data[DATABASE_TAG] = database

        if len(self.model.relations[Config.Relations.CLUSTER_RELATIONS_NAME]) == 0:
            return

        # a mongos shard can only be related to one config server
        config_server_rel = self.model.relations[Config.Relations.CLUSTER_RELATIONS_NAME][0]
        self.cluster.database_requires.update_relation_data(
            config_server_rel.id, {DATABASE_TAG: database}
        )

    def check_relation_broken_or_scale_down(self, event: RelationDepartedEvent) -> None:
        """Checks relation departed event is the result of removed relation or scale down.

        Relation departed and relation broken events occur during scaling down or during relation
        removal, only relation departed events have access to metadata to determine which case.
        """
        scaling_down = self.set_scaling_down(event)

        if scaling_down:
            logger.info(
                "Scaling down the application, no need to process removed relation in broken hook."
            )

    def is_scaling_down(self, rel_id: int) -> bool:
        """Returns True if the application is scaling down."""
        rel_departed_key = self._generate_relation_departed_key(rel_id)
        return json.loads(self.unit_peer_data[rel_departed_key])

    def has_departed_run(self, rel_id: int) -> bool:
        """Returns True if the relation departed event has run."""
        rel_departed_key = self._generate_relation_departed_key(rel_id)
        return rel_departed_key in self.unit_peer_data

    def set_scaling_down(self, event: RelationDepartedEvent) -> bool:
        """Sets whether or not the current unit is scaling down."""
        # check if relation departed is due to current unit being removed. (i.e. scaling down the
        # application.)
        rel_departed_key = self._generate_relation_departed_key(event.relation.id)
        scaling_down = event.departing_unit == self.unit
        self.unit_peer_data[rel_departed_key] = json.dumps(scaling_down)
        return scaling_down

    def proceed_on_broken_event(self, event) -> bool:
        """Returns True if relation broken event should be acted on.."""
        # Only relation_deparated events can check if scaling down
        departed_relation_id = event.relation.id
        if not self.has_departed_run(departed_relation_id):
            logger.info(
                "Deferring, must wait for relation departed hook to decide if relation should be removed."
            )
            event.defer()
            return False

        # check if were scaling down and add a log message
        if self.is_scaling_down(departed_relation_id):
            logger.info(
                "Relation broken event occurring due to scale down, do not proceed to remove users."
            )
            return False

        return True

    def get_mongos_host(self) -> str:
        """Returns the host for mongos as a str.

        The host for mongos can be either the Unix Domain Socket or an IP address depending on how
        the client wishes to connect to mongos (inside Juju or outside).
        """
        return self.unit_host(self.unit)

    @staticmethod
    def _generate_relation_departed_key(rel_id: int) -> str:
        """Generates the relation departed key for a specified relation id."""
        return f"relation_{rel_id}_departed"

    def open_mongos_port(self) -> None:
        """Opens the mongos port for TCP connections."""
        self.unit.open_port("tcp", Config.MONGOS_PORT)

    def is_role(self, role_name: str) -> bool:
        """Checks if application is running in provided role."""
        return self.role == role_name

    def is_db_service_ready(self) -> bool:
        """Returns True if the underlying database service is ready."""
        with MongosConnection(self.mongos_config) as mongos:
            return mongos.is_ready

    def _push_keyfile_to_workload(self, container: Container) -> None:
        """Upload the keyFile to a workload container."""
        keyfile = self.get_secret(APP_SCOPE, Config.Secrets.SECRET_KEYFILE_NAME)
        if not keyfile:
            raise MissingSecretError(f"No secret defined for {APP_SCOPE}, keyfile")
        else:
            self.push_file_to_unit(
                container=container,
                parent_dir=Config.MONGOD_CONF_DIR,
                file_name=Config.TLS.KEY_FILE_NAME,
                file_contents=keyfile,
            )

    @staticmethod
    def _pull_licenses(container: Container) -> None:
        """Pull licences from workload."""
        licenses = [
            "snap",
            "rock",
            "percona-server",
        ]

        for license_name in licenses:
            try:
                license_file = container.pull(path=Config.get_license_path(license_name))
                f = open(f"LICENSE_{license_name}", "x")
                f.write(str(license_file.read()))
                f.close()
            except FileExistsError:
                pass

    @staticmethod
    def _set_data_dir_permissions(container: Container) -> None:
        """Ensure the data directory for mongodb is writable for the "mongodb" user.

        Until the ability to set fsGroup and fsGroupChangePolicy via Pod securityContext
        is available, we fix permissions incorrectly with chown.
        """
        for path in [Config.DATA_DIR]:
            paths = container.list_files(path, itself=True)
            assert len(paths) == 1, "list_files doesn't return only the directory itself"
            logger.debug(f"Data directory ownership: {paths[0].user}:{paths[0].group}")
            if paths[0].user != Config.UNIX_USER or paths[0].group != Config.UNIX_GROUP:
                container.exec(
                    f"chown {Config.UNIX_USER}:{Config.UNIX_GROUP} -R {path}".split(" ")
                )

    def push_file_to_unit(
        self,
        parent_dir: str,
        file_name: str,
        file_contents: str,
        container: Container = None,
    ) -> None:
        """Push the file on the container, with the right permissions."""
        container = container or self.unit.get_container(Config.CONTAINER_NAME)
        container.push(
            f"{parent_dir}/{file_name}",
            file_contents,
            make_dirs=True,
            permissions=0o400,
            user=Config.UNIX_USER,
            group=Config.UNIX_GROUP,
        )

    def unit_host(self, unit: Unit) -> str:
        """Create a DNS name for a MongoDB unit.

        Args:
            unit_name: the juju unit name, e.g. "mongodb/1".

        Returns:
            A string representing the hostname of the MongoDB unit.
        """
        unit_id = unit.name.split("/")[1]
        return f"{self.app.name}-{unit_id}.{self.app.name}-endpoints"

    # END: helper functions

    # BEGIN: properties
    @property
    def _mongos_layer(self) -> Layer:
        """Returns a Pebble configuration layer for mongos."""

        if not self.cluster.get_config_server_uri():
            logger.error("cannot start mongos without a config_server_db")
            raise MissingConfigServerError()

        layer_config = {
            "summary": "mongos layer",
            "description": "Pebble config layer for mongos router",
            "services": {
                "mongos": {
                    "override": "replace",
                    "summary": "mongos",
                    "command": "mongos "
                    + get_mongos_args(
                        self.mongos_config,
                        snap_install=False,
                        config_server_db=self.cluster.get_config_server_uri(),
                    ),
                    "startup": "enabled",
                    "user": Config.UNIX_USER,
                    "group": Config.UNIX_GROUP,
                }
            },
        }
        return Layer(layer_config)  # type: ignore

    @property
    def mongos_initialised(self) -> bool:
        """Check if mongos is initialised."""
        return "mongos_initialised" in self.app_peer_data

    @mongos_initialised.setter
    def mongos_initialised(self, value: bool):
        """Set the mongos_initialised flag."""
        if value:
            self.app_peer_data["mongos_initialised"] = str(value)
        elif "mongos_initialised" in self.app_peer_data:
            del self.app_peer_data["mongos_initialised"]

    @property
    def _unit_ip(self) -> str:
        """Returns the ip address of the unit."""
        return str(self.model.get_binding(Config.Relations.PEERS).network.bind_address)

    @property
    def is_external_client(self) -> Optional[str]:
        """Returns the connectivity mode which mongos should use.

        Note that for K8s routers this should always default to True. However we still include
        this function so that we can have parity on properties with the K8s and VM routers.
        """
        return True

    @property
    def database(self) -> Optional[str]:
        """Returns a database to be used by mongos admin user.

        TODO: Future PR. There should be a separate function with a mapping of databases for the
        associated clients.
        """
        return f"{self.app.name}_{self.model.name}"

    @property
    def extra_user_roles(self) -> Set[str]:
        """Returns the user roles of the mongos charm.

        TODO: Future PR. There should be a separate function with a mapping of roles for the
        associated clients.
        """
        return Config.USER_ROLE_CREATE_USERS

    @property
    def mongos_config(self) -> MongosConfiguration:
        """Generates a MongoDBConfiguration object for mongos in the deployment of MongoDB."""
        hosts = [self.get_mongos_host()]
        external_ca, _ = self.tls.get_tls_files(internal=False)
        internal_ca, _ = self.tls.get_tls_files(internal=True)

        return MongosConfiguration(
            database=self.database,
            username=self.get_secret(APP_SCOPE, Config.Secrets.USERNAME),
            password=self.get_secret(APP_SCOPE, Config.Secrets.PASSWORD),
            hosts=hosts,
            # unlike the vm mongos charm, the K8s charm does not communicate with the unix socket
            port=Config.MONGOS_PORT,
            roles=self.extra_user_roles,
            tls_external=external_ca is not None,
            tls_internal=internal_ca is not None,
        )

    @property
    def _peers(self) -> Relation | None:
        """Fetch the peer relation.

        Returns:
             An `ops.model.Relation` object representing the peer relation.
        """
        return self.model.get_relation(Config.Relations.PEERS)

    @property
    def unit_peer_data(self) -> Dict:
        """Peer relation data object."""
        if not self._peers:
            return {}

        return self._peers.data[self.unit]

    @property
    def app_peer_data(self) -> Dict:
        """Peer relation data object."""
        if not self._peers:
            return {}

        return self._peers.data[self.app]

    @property
    def upgrade_in_progress(self) -> bool:
        """Returns true if an upgrade is currently in progress.

        TODO implement this function once upgrades are supported.
        """
        return False

    @property
    def config_server_db(self) -> str:
        """Fetch current the config server database that this unit is connected to."""
        if not self.is_integrated_to_config_server():
            return ""

        return self.model.get_relation(Config.Relations.CLUSTER_RELATIONS_NAME).app.name

    # END: properties


if __name__ == "__main__":
    ops.main(MongosCharm)
