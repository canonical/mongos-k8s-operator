# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.

"""In this class, we manage relations between config-servers and shards.

This class handles the sharing of secrets between sharded components, adding shards, and removing
shards.
"""
import logging
from ops.framework import Object
from ops.charm import CharmBase
from charms.data_platform_libs.v0.data_interfaces import (
    DatabaseProvides,
)

from charms.mongodb.v1.mongos import MongosConfiguration

logger = logging.getLogger(__name__)
DATABASE_KEY = "database"
USER_ROLES_KEY = "extra-user-roles"
MONGOS_RELATION_NAME = "mongos_proxy"
EXTERNAL_CONNECTIVITY_TAG = "external-node-connectivity"

# TODO - the below LIBID, LIBAPI, and LIBPATCH are not valid and were made manually. These will be
# created automatically once the charm has been published. The charm has not yet been published
# due to:
# https://discourse.charmhub.io/t/request-ownership-of-reserved-mongos-charm/12735

# The unique Charmhub library identifier, never change it
LIBID = "58ad1ccca4974932ba22b97781b9b2a0"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2

"""Library to manage the relation for the application between mongos and the deployed application.
In short, this relation ensure that:
1. mongos receives the specified database and users roles needed by the host application
2. the host application receives the generated username, password and uri for connecting to the 
sharded cluster.

This library contains the Requires and Provides classes for handling the relation between an
application and mongos. The mongos application relies on the MongosProvider class and the deployed
application uses the MongoDBRequires class.

The following is an example of how to use the MongoDBRequires class to specify the roles and 
database name:

```python
from charms.mongos.v0.mongos_client_interface import MongosRequirer


class ApplicationCharm(CharmBase):

    def __init__(self, *args):
        super().__init__(*args)

        # relation events for mongos client
        self._mongos_client = MongosRequirer(
            self,
            database_name="my-test-db",
            extra_user_roles="admin",
        )
```

To receive the username, password, and uri:
# TODO this is to be implemented in a future PR
"""


class MongosProvider(Object):
    """Manage relations between the mongos router and the application on the mongos side."""

    def __init__(
        self, charm: CharmBase, relation_name: str = MONGOS_RELATION_NAME
    ) -> None:
        """Constructor for MongosProvider object."""
        self.relation_name = relation_name
        self.charm = charm
        self.database_provides = DatabaseProvides(
            self.charm, relation_name=self.relation_name
        )

        super().__init__(charm, self.relation_name)
        self.framework.observe(
            charm.on[self.relation_name].relation_changed, self._on_relation_changed
        )

        # TODO Future PRs handle relation broken

    def _on_relation_changed(self, event) -> None:
        """Handles updating the database and extra user roles."""
        if not self.charm.unit.is_leader():
            return

        new_database_name = (
            self.database_provides.fetch_relation_field(event.relation.id, DATABASE_KEY)
            or self.charm.database
        )
        new_extra_user_roles = (
            self.database_provides.fetch_relation_field(
                event.relation.id, USER_ROLES_KEY
            )
            or self.charm.extra_user_roles
        )
        external_connectivity = (
            self.database_provides.fetch_relation_field(
                event.relation.id, EXTERNAL_CONNECTIVITY_TAG
            )
            == "true"
        )

        if new_database_name != self.charm.database:
            self.charm.set_database(new_database_name)

        if new_extra_user_roles != self.charm.extra_user_roles:
            if isinstance(new_extra_user_roles, str):
                new_extra_user_roles = [new_extra_user_roles]

            self.charm.set_user_roles(new_extra_user_roles)

        self.charm.set_external_connectivity(external_connectivity)
        if external_connectivity:
            self.charm.open_mongos_port()

    def remove_connection_info(self) -> None:
        """Sends the URI to the related parent application"""
        logger.info("Removing connection information from host application.")
        for relation in self.model.relations[MONGOS_RELATION_NAME]:
            self.database_provides.delete_relation_data(
                relation.id, fields=["username", "password", "uris"]
            )

    def update_connection_info(self, config: MongosConfiguration) -> None:
        """Sends the URI to the related parent application"""
        logger.info("Sharing connection information to host application.")
        for relation in self.model.relations[MONGOS_RELATION_NAME]:
            self.database_provides.set_credentials(
                relation.id, config.username, config.password
            )
            self.database_provides.set_database(relation.id, config.database)
            self.database_provides.set_uris(
                relation.id,
                config.uri,
            )
