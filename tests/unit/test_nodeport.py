# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import unittest
import pytest

from unittest import mock
from unittest.mock import patch, PropertyMock
import httpx
from ops.testing import Harness
from single_kernel_mongo.lib.charms.data_platform_libs.v0.data_interfaces import (
    DatabaseRequiresEvents,
)
from single_kernel_mongo.exceptions import DeployedWithoutTrustError
from charm import MongosK8sCharm

from lightkube.core.exceptions import ApiError

logger = logging.getLogger(__name__)


STATUS_JUJU_TRUST = (
    "Insufficient permissions, try: `juju trust mongos-k8s --scope=cluster`"
)
CLUSTER_ALIAS = "cluster"


@pytest.fixture(autouse=True)
def patch_upgrades(monkeypatch):
    monkeypatch.setattr(
        "single_kernel_mongo.managers.k8s.K8sManager.get_pod",
        lambda *args, **kwargs: 0,
    )


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

        self.harness = Harness(MongosK8sCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    @patch("single_kernel_mongo.managers.k8s.K8sManager.get_service")
    def test_delete_unit_service_has_no_metadata(self, get_service):
        """Verify that when no metadata is present, the charm raises an error."""
        service = mock.Mock()
        service.metadata = None
        get_service.return_value = service

        with self.assertRaises(Exception):
            self.harness.charm.operator.k8s.delete_service()

    @patch(
        "single_kernel_mongo.managers.k8s.K8sManager.client",
        new_callable=PropertyMock(),
    )
    @patch("single_kernel_mongo.managers.k8s.K8sManager.get_service")
    def test_delete_unit_service_raises_ApiError(self, get_service, mock_client):
        """Verify that when charm needs juju trust a status is logged."""
        metadata_mock = mock.Mock()
        metadata_mock.name = "service-name"
        service = mock.Mock()
        service.metadata = metadata_mock
        get_service.return_value = service

        # We need a valid API error due to error handling in lightkube
        api_error = ApiError(
            request=httpx.Request(url="http://controller/call", method="DELETE"),
            response=httpx.Response(409, json={"message": "bad call"}),
        )

        delete_mock = mock.Mock()
        delete_mock.side_effect = api_error
        mock_client.delete = delete_mock

        with self.assertRaises(ApiError):
            self.harness.charm.operator.k8s.delete_service()

    @patch(
        "single_kernel_mongo.managers.k8s.K8sManager.client",
        new_callable=PropertyMock(),
    )
    @patch("single_kernel_mongo.managers.k8s.K8sManager.get_service")
    def test_delete_unit_service_needs_juju_trust(self, get_service, mock_client):
        """Verify that when charm needs juju trust a status is logged."""
        metadata_mock = mock.Mock()
        metadata_mock.name = "service-name"
        service = mock.Mock()
        service.metadata = metadata_mock
        get_service.return_value = service

        # We need a valid API error due to error handling in lightkube
        api_error = ApiError(
            request=httpx.Request(url="http://controller/call", method="DELETE"),
            response=httpx.Response(409, json={"message": "bad call", "code": 403}),
        )

        delete_mock = mock.Mock()
        delete_mock.side_effect = api_error
        mock_client.delete = delete_mock

        with self.assertRaises(DeployedWithoutTrustError):
            self.harness.charm.operator.k8s.delete_service()
