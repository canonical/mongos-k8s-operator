#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Application charm that connects to database charms.

This charm is meant to be used only for testing
of the libraries in this repository.
"""

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus


from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires

logger = logging.getLogger(__name__)

# Extra roles that this application needs when interacting with the database.
EXTRA_USER_ROLES = "admin"


class ApplicationCharm(CharmBase):
    """Application charm that connects to database charms."""

    def __init__(self, *args):
        super().__init__(*args)
        # Default charm events.
        self.framework.observe(self.on.start, self._on_start)

        # relation events for mongos client
        self.database = DatabaseRequires(
            self,
            relation_name="mongos",
            database_name="my-test-db",
            extra_user_roles=EXTRA_USER_ROLES,
        )

    def _on_start(self, _) -> None:
        """Only sets an Active status."""
        self.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(ApplicationCharm)
