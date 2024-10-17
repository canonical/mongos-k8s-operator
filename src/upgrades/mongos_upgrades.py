#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.
"""Kubernetes Upgrade Code.

This code is slightly different from the code which was written originally.
It is required to deploy the application with `--trust` for this code to work
as it has to interact with the Kubernetes StatefulSet.
The main differences are:
 * Add the handling of workload version + version sharing on the cluster in the
 upgrade handler + relation created handler.
 * Add the two post upgrade events that check the cluster health and run it if
 we are in state `RESTARTING`.
"""

from logging import getLogger
from typing import TYPE_CHECKING

from charms.mongos.v0.upgrade_helpers import (
    PEER_RELATION_ENDPOINT_NAME,
    ROLLBACK_INSTRUCTIONS,
    GenericMongosUpgrade,
    PeerRelationNotReady,
    PrecheckFailed,
    UnitState,
)
from ops import ActiveStatus
from ops.charm import ActionEvent
from ops.framework import EventBase, EventSource
from ops.model import BlockedStatus
from overrides import override

from config import Config
from upgrades.kubernetes_upgrades import KubernetesUpgrade

if TYPE_CHECKING:
    from charm import MongosCharm

logger = getLogger()

PRECHECK_ACTION_NAME = "pre-refresh-check"


class _PostUpgradeCheckMongoDB(EventBase):
    """Run post upgrade check on MongoDB to verify that the cluster is healhty."""

    def __init__(self, handle):
        super().__init__(handle)


class MongosUpgrade(GenericMongosUpgrade):
    """Handlers for upgrade events."""

    post_app_upgrade_event = EventSource(_PostUpgradeCheckMongoDB)

    def __init__(self, charm: "MongosCharm"):
        self.charm = charm
        super().__init__(charm, PEER_RELATION_ENDPOINT_NAME)

    @override
    def _observe_events(self, charm: "MongosCharm") -> None:
        self.framework.observe(
            charm.on[PEER_RELATION_ENDPOINT_NAME].relation_created,
            self._on_upgrade_peer_relation_created,
        )
        self.framework.observe(
            charm.on[PEER_RELATION_ENDPOINT_NAME].relation_changed,
            self._reconcile_upgrade,
        )
        self.framework.observe(
            charm.on[PRECHECK_ACTION_NAME].action, self._on_pre_upgrade_check_action
        )
        self.framework.observe(
            self.post_app_upgrade_event, self.run_post_app_upgrade_task
        )
        self.framework.observe(
            charm.on["force-refresh-start"].action, self._on_force_upgrade_action
        )

    def _on_force_upgrade_action(self, event: ActionEvent):
        if not self.charm.unit.is_leader():
            message = f"Must run action on leader unit. (e.g. `juju run {self.charm.app.name}/leader force-refresh-start`)"
            logger.debug(f"Force refresh failed: {message}")
            event.fail(message)
            return
        if not self._upgrade or not self._upgrade.in_progress:
            message = "No upgrade in progress"
            logger.debug(f"Force refresh failed: {message}")
            event.fail(message)
            return
        self._upgrade.reconcile_partition(action_event=event)

    def _reconcile_upgrade(
        self, event: EventBase, during_upgrade: bool = False
    ) -> None:
        """Handle upgrade events."""
        if not self._upgrade:
            logger.debug("Peer relation not available")
            return
        if not self._upgrade.versions_set:
            logger.debug("Peer relation not ready")
            return
        if self.charm.unit.is_leader() and not self._upgrade.in_progress:
            # Run before checking `self._upgrade.is_compatible` in case incompatible upgrade was
            # forced & completed on all units.
            self._upgrade.set_versions_in_app_databag()

        if self._upgrade.unit_state is UnitState.RESTARTING:  # Kubernetes only
            if not self._upgrade.is_compatible:
                logger.info(
                    "Refresh incompatible. If you accept potential *data loss* and *downtime*, you can continue with `force-refresh-start`"
                )
                self.charm.status.set_and_share_status(
                    Config.Status.INCOMPATIBLE_UPGRADE
                )
                return
        if (
            not during_upgrade
            and self.charm.db_initialised
            and self.charm.is_db_service_ready()
        ):
            self._upgrade.unit_state = UnitState.HEALTHY
            self.charm.status.set_and_share_status(ActiveStatus())
        if self.charm.unit.is_leader():
            self._upgrade.reconcile_partition()

        self._set_upgrade_status()

    def _set_upgrade_status(self):
        if self.charm.unit.is_leader():
            self.charm.app.status = self._upgrade.app_status or ActiveStatus()
        # Set/clear upgrade unit status if no other unit status - upgrade status for units should
        # have the lowest priority.
        if (
            isinstance(self.charm.unit.status, ActiveStatus)
            or (
                isinstance(self.charm.unit.status, BlockedStatus)
                and self.charm.unit.status.message.startswith(
                    "Rollback with `juju refresh`. Pre-refresh check failed:"
                )
            )
            or self.charm.unit.status == Config.Status.WAITING_POST_UPGRADE_STATUS
        ):
            self.charm.status.set_and_share_status(
                self._upgrade.get_unit_juju_status() or ActiveStatus()
            )

    def _on_upgrade_peer_relation_created(self, _) -> None:
        if self.charm.unit.is_leader():
            self._upgrade.set_versions_in_app_databag()

    def run_post_app_upgrade_task(self, event: EventBase):
        """Runs post-upgrade checks for after mongos router upgrade."""
        # The mongos service cannot be considered ready until it has a config-server. Therefore
        # it is not necessary to do any sophisticated checks.
        if not self.charm.mongos_initialised:
            self._upgrade.unit_state = UnitState.HEALTHY
            return

        self.run_post_upgrade_checks(event)

    def _on_pre_upgrade_check_action(self, event: ActionEvent) -> None:
        if not self.charm.unit.is_leader():
            message = f"Must run action on leader unit. (e.g. `juju run {self.charm.app.name}/leader {PRECHECK_ACTION_NAME}`)"
            logger.debug(f"Pre-refresh check failed: {message}")
            event.fail(message)
            return
        if not self._upgrade or self._upgrade.in_progress:
            message = "Upgrade already in progress"
            logger.debug(f"Pre-refresh check failed: {message}")
            event.fail(message)
            return
        try:
            self._upgrade.pre_upgrade_check()
        except PrecheckFailed as exception:
            message = f"Charm is *not* ready for refresh. Pre-refresh check failed: {exception.message}"
            logger.debug(f"Pre-refresh check failed: {message}")
            event.fail(message)
            return
        message = "Charm is ready for refresh"
        event.set_results({"result": message})
        logger.debug(f"Pre-refresh check succeeded: {message}")

    @property
    @override
    def _upgrade(self) -> KubernetesUpgrade | None:
        try:
            return KubernetesUpgrade(self.charm)
        except PeerRelationNotReady:
            return None

    def run_post_upgrade_checks(self, event: EventBase) -> None:
        """Runs post-upgrade checks for after a shard/config-server/replset/cluster upgrade."""
        logger.debug("Checking mongos running after refresh.")
        if not self.charm.cluster.is_mongos_running():
            logger.debug(
                "Waiting for mongos router to be ready before finalising refresh."
            )
            event.defer()
            return

        logger.debug("Checking mongos is able to read/write after refresh.")
        if not self.is_mongos_able_to_read_write():
            logger.error("mongos is not able to read/write after refresh.")
            logger.info(ROLLBACK_INSTRUCTIONS)
            self.charm.status.set_and_share_status(Config.Status.UNHEALTHY_UPGRADE)
            event.defer()
            return

        if self.charm.unit.status == Config.Status.UNHEALTHY_UPGRADE:
            self.charm.status.set_and_share_status(ActiveStatus())

        logger.debug("Refresh of unit succeeded.")
        self._upgrade.unit_state = UnitState.HEALTHY
