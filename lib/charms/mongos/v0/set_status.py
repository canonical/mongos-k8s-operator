#!/usr/bin/env python3
"""Code for handing statuses in the app and unit."""
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from ops.charm import CharmBase
from ops.framework import Object
from ops.model import StatusBase


# The unique Charmhub library identifier, never change it
LIBID = "0713372afa0841359edbb777273ecdbf"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


class MongosStatusHandler(Object):
    """Verifies versions across multiple integrated applications."""

    def __init__(
        self,
        charm: CharmBase,
    ) -> None:
        """Constructor for CrossAppVersionChecker.

        Args:
            charm: charm to inherit from.
        """
        super().__init__(charm, None)
        self.charm = charm

        # TODO Future Feature/Epic: handle update_status

    # BEGIN Helpers

    def set_and_share_status(self, status: StatusBase):
        """Sets the charm status and shares it where applicable."""
        # TODO Future Feature/Epic: process other statuses, i.e. only set provided status if its
        # appropriate.
        self.charm.unit.status = status

        self.set_app_status()

    def set_app_status(self):
        """TODO Future Feature/Epic: parse statuses and set a status for the entire app."""

    # END: Helpers
