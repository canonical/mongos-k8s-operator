# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import unittest
from unittest import mock
from unittest.mock import patch, PropertyMock
from ops.model import BlockedStatus
from ops.testing import Harness
from node_port import NodePortManager, ApiError
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequiresEvents
from charm import MongosCharm


logger = logging.getLogger(__name__)


STATUS_JUJU_TRUST = (
    "Insufficient permissions, try: `juju trust mongos-k8s --scope=cluster`"
)
CLUSTER_ALIAS = "cluster"


class TestNodePort(unittest.TestCase):
    def setUp(self, *unused):
        """Set up the charm for each unit test."""
        try:
            # runs before each test to delete the custom events created for the aliases. This is
            # needed because the events are created again in the next test, which causes an error
            # related to duplicated events.
            delattr(DatabaseRequiresEvents, f"{CLUSTER_ALIAS}_database_created")
            delattr(DatabaseRequiresEvents, f"{CLUSTER_ALIAS}_endpoints_changed")
            delattr(
                DatabaseRequiresEvents, f"{CLUSTER_ALIAS}_read_only_endpoints_changed"
            )
        except AttributeError:
            # Ignore the events not existing before the first test.
            pass

        self.harness = Harness(MongosCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch("charm.NodePortManager.get_service")
    def test_delete_unit_service_has_no_metadata(self, get_service):
        """Verify that when no metadata is present, the charm raises an error."""
        service = mock.Mock()
        service.metadata = None
        get_service.return_value = service

        with self.assertRaises(Exception):
            self.harness.charm.node_port_manager.delete_unit_service()

    @patch("charm.NodePortManager.get_service")
    @patch("charm.NodePortManager.client", new_callable=PropertyMock)
    def test_delete_unit_service_raises_ApiError(self, client, get_service):
        """Verify that when charm needs juju trust a status is logged."""
        metadata_mock = mock.Mock()
        metadata_mock.name = "serice-name"
        service = mock.Mock()
        service.metadata = metadata_mock
        get_service.return_value = service

        client.return_value.delete.side_effect = ApiError

        with self.assertRaises(ApiError):
            self.harness.charm.node_port_manager.delete_unit_service()

    @patch("src.node_port.NodePortManager.get_service")
    @patch("charm.NodePortManager.client", new_callable=PropertyMock)
    def test_delete_unit_service_needs_juju_trust(self, get_service, client):
        """Verify that when charm needs juju trust a status is logged."""

        metadata_mock = mock.Mock()
        metadata_mock.name = "serice-name"
        service = mock.Mock()
        service.metadata = metadata_mock
        get_service.return_value = service

        api_error = mock.Mock()
        api_error.status.code = 403

        NodePortManager.delete_unit_service()

        self.assertTrue(
            isinstance(self.harness.charm.unit.status, BlockedStatus(STATUS_JUJU_TRUST))
        )
