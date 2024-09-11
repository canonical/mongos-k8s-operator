#!/usr/bin/env python3
"""Charm code for `mongos` daemon."""

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from ops.main import main
import json
from exceptions import MissingSecretError, FailedToGetHostsError

from ops.pebble import PathError, ProtocolError, Layer
from node_port import (
    NodePortManager,
    ApiError,
    FailedToFindNodePortError,
    FailedToFindServiceError,
)

from typing import Set, Optional, Dict, List
from charms.mongodb.v0.config_server_interface import ClusterRequirer


from pymongo.errors import PyMongoError

from charms.mongodb.v0.mongo import MongoConfiguration, MongoConnection
from charms.mongos.v0.set_status import MongosStatusHandler
from charms.mongodb.v1.mongodb_provider import MongoDBProvider
from charms.mongodb.v0.mongodb_tls import MongoDBTLS
from charms.mongodb.v0.mongodb_secrets import SecretCache
from charms.mongodb.v0.mongodb_secrets import generate_secret_label
from charms.mongodb.v1.helpers import get_mongos_args

from config import Config

import ops
from ops.model import (
    BlockedStatus,
    WaitingStatus,
    Container,
    Relation,
    ActiveStatus,
    Unit,
)
from ops.charm import StartEvent, RelationDepartedEvent, ConfigChangedEvent

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


class ExtraDataDirError(Exception):
    """Raised when there is unexpected data in the data directory."""


class NoExternalHostError(Exception):
    """Raised when there is not an external host for a unit, when there is expected to be one."""


class MongosCharm(ops.CharmBase):
    """Charm the service."""

    def __init__(self, *args):
        super().__init__(*args)
        self.role = Config.Role.MONGOS
        self.secrets = SecretCache(self)
        self.status = MongosStatusHandler(self)
        self.node_port_manager = NodePortManager(self)

        # lifecycle events
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on.mongos_pebble_ready, self._on_mongos_pebble_ready
        )
        self.framework.observe(self.on.start, self._on_start)
        self.framework.observe(self.on.update_status, self._on_update_status)

        # when number of units change update hosts
        self.framework.observe(
            self.on.leader_elected, self._update_client_related_hosts
        )
        self.framework.observe(
            self.on[Config.Relations.PEERS].relation_joined,
            self._update_client_related_hosts,
        )
        self.framework.observe(
            self.on[Config.Relations.PEERS].relation_departed,
            self._update_client_related_hosts,
        )

        # relations
        self.tls = MongoDBTLS(self, Config.Relations.PEERS, substrate=Config.SUBSTRATE)
        self.cluster = ClusterRequirer(self, substrate=Config.SUBSTRATE)

        self.client_relations = MongoDBProvider(
            self,
            substrate=Config.SUBSTRATE,
            relation_name=Config.Relations.CLIENT_RELATIONS_NAME,
        )

    # BEGIN: hook functions
    def _on_config_changed(self, event: ConfigChangedEvent) -> None:
        """Listen to changes in the application configuration."""
        if not self.is_user_external_config_valid():
            self.set_status_invalid_external_config()
            return

        self.status.clear_status(Config.Status.INVALID_EXTERNAL_CONFIG)
        self.update_external_services()

        # toggling of external connectivity means we have to update integrated hosts
        self._update_client_related_hosts(event)

        # TODO DPE-5235 support updating data-integrator clients to have/not have public IP
        # depending on the result of the configuration

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
        if not self.is_integrated_to_config_server():
            logger.info(
                "Missing integration to config-server. mongos cannot run start sequence unless connected to config-server."
            )
            self.status.set_and_share_status(
                BlockedStatus("Missing relation to config-server.")
            )
            event.defer()
            return

        if not self.cluster.is_mongos_running():
            logger.debug("mongos service is not ready yet.")
            event.defer()
            return

        self.db_initialised = True

        if not self.unit.is_leader():
            return

        try:
            self.client_relations.oversee_users(None, None)
        except PyMongoError as e:
            logger.error(
                "Failed to create mongos client users, due to %r. Will defer and try again",
                e,
            )
            event.defer()

    def _on_update_status(self, _):
        """Handle the update status event."""
        if not self.is_user_external_config_valid():
            self.set_status_invalid_external_config()
            return

        if self.unit.status == Config.Status.UNHEALTHY_UPGRADE:
            return

        if not self.is_integrated_to_config_server():
            logger.info(
                "Missing integration to config-server. mongos cannot run unless connected to config-server."
            )
            self.status.set_and_share_status(
                BlockedStatus("Missing relation to config-server.")
            )
            return

        if tls_statuses := self.cluster.get_tls_statuses():
            self.status.set_and_share_status(tls_statuses)
            return

        # restart on high loaded databases can be very slow (e.g. up to 10-20 minutes).
        if not self.cluster.is_mongos_running():
            logger.info("mongos has not started yet")
            self.status.set_and_share_status(
                WaitingStatus("Waiting for mongos to start.")
            )
            return

        self.status.set_and_share_status(ActiveStatus())

    # END: hook functions

    # BEGIN: helper functions
    def is_user_external_config_valid(self) -> bool:
        """Returns True if the user set external config is valid."""
        return (
            self.model.config["expose-external"]
            in Config.ExternalConnections.VALID_EXTERNAL_CONFIG
        )

    def set_status_invalid_external_config(self) -> None:
        """Sets status for invalid external configuration."""
        logger.error(
            "External configuration: %s for expose-external is not valid, should be one of: %s",
            self.model.config["expose-external"],
            Config.ExternalConnections.VALID_EXTERNAL_CONFIG,
        )
        self.status.set_and_share_status(Config.Status.INVALID_EXTERNAL_CONFIG)

    def update_external_services(self) -> None:
        """Update external services based on provided configuration."""
        if (
            self.model.config["expose-external"]
            == Config.ExternalConnections.EXTERNAL_NODEPORT
        ):
            # every unit attempts to create a nodeport service - if exists, will silently continue
            self.node_port_manager.apply_service(
                service=self.node_port_manager.build_node_port_services(
                    port=Config.MONGOS_PORT
                )
            )
        else:
            self.node_port_manager.delete_unit_service()

        self.expose_external = self.model.config["expose-external"]

    def get_keyfile_contents(self) -> str | None:
        """Retrieves the contents of the keyfile on host machine."""
        # wait for keyFile to be created by leader unit
        if not self.get_secret(APP_SCOPE, Config.Secrets.SECRET_KEYFILE_NAME):
            logger.debug("waiting to receive keyfile contents from config-server.")

        try:
            container = self.unit.get_container(Config.CONTAINER_NAME)
            key = container.pull(f"{Config.MONGOD_CONF_DIR}/{Config.TLS.KEY_FILE_NAME}")
            return key.read()
        except PathError:
            logger.info("no keyfile present")
            return None

    def is_integrated_to_config_server(self) -> bool:
        """Returns True if the mongos application is integrated to a config-server."""
        return (
            self.model.get_relation(Config.Relations.CLUSTER_RELATIONS_NAME) is not None
        )

    def get_secret(self, scope: str, key: str) -> Optional[str]:
        """Get secret from the secret storage."""
        label = generate_secret_label(self, scope)
        if not (secret := self.secrets.get(label)):
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
            logger.error(
                f"Non-existing secret {scope}:{key} was attempted to be removed."
            )
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
        container.stop(Config.SERVICE_NAME)

        container.add_layer(Config.CONTAINER_NAME, self._mongos_layer, combine=True)
        container.replan()

    def set_database(self, database: str) -> None:
        """Updates the database requested for the mongos user."""
        self.app_peer_data[DATABASE_TAG] = database

        if len(self.model.relations[Config.Relations.CLUSTER_RELATIONS_NAME]) == 0:
            return

        # a mongos shard can only be related to one config server
        config_server_rel = self.model.relations[
            Config.Relations.CLUSTER_RELATIONS_NAME
        ][0]
        self.cluster.database_requires.update_relation_data(
            config_server_rel.id, {DATABASE_TAG: database}
        )

    def check_relation_broken_or_scale_down(self, event: RelationDepartedEvent) -> None:
        """Checks relation departed event is the result of removed relation or scale down.

        Relation departed and relation broken events occur during scaling down or during relation
        removal, only relation departed events have access to metadata to determine which case.
        """
        if self.set_scaling_down(event):
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

    def get_units(self) -> List[Unit]:
        units = [self.unit]
        units.extend(self.peers_units)
        return units

    def get_mongos_host(self) -> str:
        """Returns the host for mongos as a str.

        The host for mongos can be either the Unix Domain Socket or an IP address depending on how
        the client wishes to connect to mongos (inside Juju or outside).
        """
        return self.unit_host(self.unit)

    def get_ext_mongos_hosts(self) -> Set:
        """Returns the K8s hosts for mongos.

        Note: for external connections it is not enough to know the external ip, but also the
        port that is associated with the client.
        """
        hosts = set()

        if not self.is_external_client:
            return hosts

        try:
            for unit in self.get_units():
                unit_ip = self.node_port_manager.get_node_ip(unit.name)
                unit_port = self.node_port_manager.get_node_port(
                    port_to_match=Config.MONGOS_PORT, unit_name=unit.name
                )
                if unit_ip and unit_port:
                    hosts.add(f"{unit_ip}:{unit_port}")
                else:
                    raise NoExternalHostError(f"No external host for unit {unit.name}")
        except (
            NoExternalHostError,
            FailedToFindNodePortError,
            FailedToFindServiceError,
        ) as e:
            raise FailedToGetHostsError(
                "Failed to retrieve external hosts due to %s", e
            )

        return hosts

    def get_k8s_mongos_hosts(self) -> Set:
        """Returns the K8s hosts for mongos"""
        hosts = set()
        for unit in self.get_units():
            hosts.add(self.unit_host(unit))

        return hosts

    def get_mongos_hosts_for_client(self) -> Set:
        """Returns the hosts for mongos as a str.

        The host for mongos can be either the K8s pod name or an IP address depending on how
        the app has been configured.
        """
        if self.is_external_client:
            return self.get_ext_mongos_hosts()

        return self.get_k8s_mongos_hosts()

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
        with MongoConnection(self.mongos_config) as mongos:
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

    def push_tls_certificate_to_workload(self) -> None:
        """Uploads certificate to the workload container."""
        container = self.unit.get_container(Config.CONTAINER_NAME)

        # Handling of external CA and PEM files
        external_ca, external_pem = self.tls.get_tls_files(internal=False)

        if external_ca is not None:
            logger.debug("Uploading external ca to workload container")
            self.push_file_to_unit(
                container=container,
                parent_dir=Config.MONGOD_CONF_DIR,
                file_name=Config.TLS.EXT_CA_FILE,
                file_contents=external_ca,
            )
        if external_pem is not None:
            logger.debug("Uploading external pem to workload container")
            self.push_file_to_unit(
                container=container,
                parent_dir=Config.MONGOD_CONF_DIR,
                file_name=Config.TLS.EXT_PEM_FILE,
                file_contents=external_pem,
            )

        # Handling of external CA and PEM files
        internal_ca, internal_pem = self.tls.get_tls_files(internal=True)

        if internal_ca is not None:
            logger.debug("Uploading internal ca to workload container")
            self.push_file_to_unit(
                container=container,
                parent_dir=Config.MONGOD_CONF_DIR,
                file_name=Config.TLS.INT_CA_FILE,
                file_contents=internal_ca,
            )
        if internal_pem is not None:
            logger.debug("Uploading internal pem to workload container")
            self.push_file_to_unit(
                container=container,
                parent_dir=Config.MONGOD_CONF_DIR,
                file_name=Config.TLS.INT_PEM_FILE,
                file_contents=internal_pem,
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
                license_file = container.pull(
                    path=Config.get_license_path(license_name)
                )
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
            if not len(paths) == 1:
                raise ExtraDataDirError(
                    "list_files doesn't return only the directory itself"
                )
            logger.debug(f"Data directory ownership: {paths[0].user}:{paths[0].group}")
            if paths[0].user != Config.UNIX_USER or paths[0].group != Config.UNIX_GROUP:
                container.exec(
                    f"chown {Config.UNIX_USER}:{Config.UNIX_GROUP} -R {path}".split()
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

    def delete_tls_certificate_from_workload(self) -> None:
        """Deletes certificate from the workload container."""
        logger.info("Deleting TLS certificate from workload container")
        container = self.unit.get_container(Config.CONTAINER_NAME)
        for file in [
            Config.TLS.EXT_CA_FILE,
            Config.TLS.EXT_PEM_FILE,
            Config.TLS.INT_CA_FILE,
            Config.TLS.INT_PEM_FILE,
        ]:
            try:
                container.remove_path(f"{Config.MONGOD_CONF_DIR}/{file}")
            except PathError as err:
                logger.debug("Path unavailable: %s (%s)", file, str(err))

    def unit_host(self, unit: Unit) -> str:
        """Create a DNS name for a MongoDB unit.

        Args:
            unit_name: the juju unit name, e.g. "mongodb/1".

        Returns:
            A string representing the hostname of the MongoDB unit.
        """
        unit_id = unit.name.split("/")[1]
        return f"{self.app.name}-{unit_id}.{self.app.name}-endpoints"

    def get_config_server_name(self) -> Optional[str]:
        """Returns the name of the Juju Application that mongos is using as a config server."""
        return self.cluster.get_config_server_name()

    def has_config_server(self) -> bool:
        """Returns True is the mongos router is integrated to a config-server."""
        return self.cluster.get_config_server_name() is not None

    def _update_client_related_hosts(self, event) -> None:
        """Update hosts of client relations."""
        if not self.db_initialised:
            return

        if not self.unit.is_leader():
            return

        try:
            self.client_relations.update_app_relation_data()
        except PyMongoError as e:
            logger.error("Deferring on updating app relation data since: error: %r", e)
            event.defer()
            return
        except ApiError as e:
            if e.status.code == 404:
                # it is possible that a unit is enabling node port when we try to update hosts
                logger.debug(
                    "Deferring on updating app relation data since service not found for more or one units"
                )
                event.defer()
                return

            raise

    # END: helper functions

    # BEGIN: properties
    @property
    def expose_external(self) -> Optional[str]:
        """Returns mode of exposure for external connections."""
        if (
            self.app_peer_data.get("expose-external", Config.ExternalConnections.NONE)
            == Config.ExternalConnections.NONE
        ):
            return None

        return self.app_peer_data["expose-external"]

    @expose_external.setter
    def expose_external(self, expose_external: str) -> None:
        """Set the expose_external flag."""
        if not self.unit.is_leader():
            return

        if expose_external not in Config.ExternalConnections.VALID_EXTERNAL_CONFIG:
            return

        self.app_peer_data["expose-external"] = expose_external

    @property
    def db_initialised(self) -> bool:
        """Check if mongos is initialised.

        Named `db_initialised` rather than `router_initialised` due to need for parity across DB
        charms.
        """
        return json.loads(self.app_peer_data.get("db_initialised", "false"))

    @db_initialised.setter
    def db_initialised(self, value):
        """Set the db_initalised flag."""
        if not self.unit.is_leader():
            return

        if isinstance(value, bool):
            self.app_peer_data["db_initialised"] = json.dumps(value)
        else:
            raise ValueError(
                f"'db_initialised' must be a boolean value. Proivded: {value} is of type {type(value)}"
            )

    @property
    def peers_units(self) -> list[Unit]:
        """Get peers units in a safe way."""
        if not self._peers:
            return []
        else:
            return self._peers.units

    @property
    def _mongos_layer(self) -> Layer:
        """Returns a Pebble configuration layer for mongos."""
        if not (get_config_server_uri := self.cluster.get_config_server_uri()):
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
                        config_server_db=get_config_server_uri,
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
    def is_external_client(self) -> bool:
        """Returns the connectivity mode which mongos should use.

        Note that for K8s routers this should always default to True. However we still include
        this function so that we can have parity on properties with the K8s and VM routers.
        """
        return self.expose_external == Config.ExternalConnections.EXTERNAL_NODEPORT

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
    def mongo_config(self) -> MongoConfiguration:
        """Returns a MongoConfiguration object for shared libs with agnostic mongo commands."""
        return self.mongos_config

    @property
    def mongos_config(self) -> MongoConfiguration:
        """Generates a MongoDBConfiguration object for mongos in the deployment of MongoDB."""
        external_ca, _ = self.tls.get_tls_files(internal=False)
        internal_ca, _ = self.tls.get_tls_files(internal=True)

        return MongoConfiguration(
            database=self.database,
            username=self.get_secret(APP_SCOPE, Config.Secrets.USERNAME),
            password=self.get_secret(APP_SCOPE, Config.Secrets.PASSWORD),
            hosts=self.get_k8s_mongos_hosts(),
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
        if not (
            config_server_relation := self.model.get_relation(
                Config.Relations.CLUSTER_RELATIONS_NAME
            )
        ):
            return ""

        return config_server_relation.app.name

    # END: properties


if __name__ == "__main__":
    main(MongosCharm)
