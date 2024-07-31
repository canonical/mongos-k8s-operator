#!/usr/bin/env python3
"""Charm code for mongos service on Kubernetes."""

# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
from ops.charm import CharmBase
from ops.main import main


class MongosCharm(CharmBase):
    """A Juju Charm to deploy mongos on Kubernetes."""

    def __init__(self, *args):
        """Listen to juju events and pair them with their associated function."""
        super().__init__(*args)


if __name__ == "__main__":
    main(MongosCharm)
