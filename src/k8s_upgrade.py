#!/usr/bin/env python3
"""K8S Upgrade code // rough draft only for pre upgrade checks."""
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

from functools import cached_property
from logging import getLogger
from typing import List, TYPE_CHECKING

import lightkube
import lightkube.models.apps_v1
import lightkube.resources.apps_v1
import lightkube.resources.core_v1
from charms.mongos.v0.upgrade_helpers import (
    PEER_RELATION_ENDPOINT_NAME,
    ROLLBACK_INSTRUCTIONS,
    AbstractUpgrade,
    GenericMongosUpgrade,
    PeerRelationNotReady,
    PrecheckFailed,
    UnitState,
    unit_number,
)
from lightkube.core.exceptions import ApiError
from ops import ActiveStatus, StatusBase
from ops.charm import ActionEvent
from ops.framework import EventBase, EventSource
from ops.model import BlockedStatus, Unit
from overrides import override

from config import Config

if TYPE_CHECKING:
    from charm import MongosCharm

logger = getLogger()

PRECHECK_ACTION_NAME = "pre-refresh-check"


class DeployedWithoutTrust(Exception):
    """Deployed without `juju deploy --trust` or `juju trust`.

    Needed to access Kubernetes StatefulSet.
    """

    def __init__(self, *, app_name: str):
        super().__init__(
            f"Run `juju trust {app_name} --scope=cluster` and `juju resolve` for each unit (or remove & re-deploy {app_name} with `--trust`)"
        )


class _Partition:
    """StatefulSet partition getter/setter."""

    # Note: I realize this isn't very Pythonic (it'd be nicer to use a property). Because of how
    # ops is structured, we don't have access to the app name when we initialize this class. We
    # need to only initialize this class once so that there is a single cache. Therefore, the app
    # name needs to be passed as argument to the methods (instead of as an argument to __init__)—
    # so we can't use a property.

    def __init__(self):
        # Cache lightkube API call for duration of charm execution
        self._cache: dict[str, int] = {}

    def get(self, *, app_name: str) -> int:
        return self._cache.setdefault(
            app_name,
            lightkube.Client()
            .get(res=lightkube.resources.apps_v1.StatefulSet, name=app_name)
            .spec.updateStrategy.rollingUpdate.partition,
        )

    def set(self, *, app_name: str, value: int) -> None:
        lightkube.Client().patch(
            res=lightkube.resources.apps_v1.StatefulSet,
            name=app_name,
            obj={"spec": {"updateStrategy": {"rollingUpdate": {"partition": value}}}},
        )
        self._cache[app_name] = value


class KubernetesUpgrade(AbstractUpgrade):
    """Code for Kubernetes Upgrade.

    This is the implementation of Kubernetes Upgrade methods.
    """

    def __init__(self, charm: "MongosCharm", *args, **kwargs):
        try:
            partition.get(app_name=charm.app.name)
        except ApiError as err:
            if err.status.code == 403:
                raise DeployedWithoutTrust(app_name=charm.app.name)
            raise
        super().__init__(charm, *args, **kwargs)

    @override
    def _get_unit_healthy_status(self) -> StatusBase:
        version = self._unit_workload_container_versions[self._unit.name]
        if version == self._app_workload_container_version:
            return ActiveStatus(
                f'MongoDB {self._current_versions["workload"]} running; Charm revision {self._current_versions["charm"]}'
            )
        return ActiveStatus(
            f'MongoDB {self._current_versions["workload"]} running (restart pending); Charm revision {self._current_versions["charm"]}'
        )

    @property
    def authorized(self) -> bool:
        raise Exception("Not supported on Kubernetes")

    def upgrade_unit(self, *, charm) -> None:
        raise Exception("Not supported on Kubernetes")

    @property
    def _partition(self) -> int:
        """Specifies which units should upgrade.

        Unit numbers >= partition should upgrade
        Unit numbers < partition should not upgrade

        https://kubernetes.io/docs/concepts/workloads/controllers/statefulset/#partitions

        For Kubernetes, unit numbers are guaranteed to be sequential
        """
        return partition.get(app_name=self._app_name)

    @_partition.setter
    def _partition(self, value: int) -> None:
        """Sets the partition number."""
        partition.set(app_name=self._app_name, value=value)

    @cached_property  # Cache lightkube API call for duration of charm execution
    @override
    def _unit_workload_container_versions(self) -> dict[str, str]:
        """{Unit name: Kubernetes controller revision hash}.

        Even if the workload container version is the same, the workload will restart if the
        controller revision hash changes. (Juju bug: https://bugs.launchpad.net/juju/+bug/2036246).

        Therefore, we must use the revision hash instead of the workload container version. (To
        satisfy the requirement that if and only if this version changes, the workload will
        restart.)
        """
        pods = lightkube.Client().list(
            res=lightkube.resources.core_v1.Pod,
            labels={"app.kubernetes.io/name": self._app_name},
        )

        def get_unit_name(pod_name: str) -> str:
            *app_name, unit_number = pod_name.split("-")
            return f'{"-".join(app_name)}/{unit_number}'

        return {
            get_unit_name(pod.metadata.name): pod.metadata.labels[
                "controller-revision-hash"
            ]
            for pod in pods
        }

    @cached_property
    @override
    def _app_workload_container_version(self) -> str:
        """App's Kubernetes controller revision hash."""
        stateful_set = lightkube.Client().get(
            res=lightkube.resources.apps_v1.StatefulSet, name=self._app_name
        )
        return stateful_set.status.updateRevision

    def _determine_partition(
        self, units: List[Unit], action_event: ActionEvent | None
    ) -> int:
        if not self.in_progress:
            return 0
        logger.debug(f"{self._peer_relation.data=}")
        for unit in units:
            # Note: upgrade_order_index != unit number
            state = self._peer_relation.data[unit].get("state")
            if state:
                state = UnitState(state)
            if (
                not action_event and state is not UnitState.HEALTHY
            ) or self._unit_workload_container_versions[
                unit.name
            ] != self._app_workload_container_version:
                return unit_number(unit)
        return 0

    def reconcile_partition(self, *, action_event: ActionEvent | None = None) -> None:  # noqa: C901
        """If ready, lower partition to upgrade next unit.

        If upgrade is not in progress, set partition to 0. (If a unit receives a stop event, it may
        raise the partition even if an upgrade is not in progress.)

        Automatically upgrades next unit if all upgraded units are healthy—except if only one unit
        has upgraded (need manual user confirmation [via Juju action] to upgrade next unit)

        Handle Juju action to:
        - confirm first upgraded unit is healthy and resume upgrade
        - force upgrade of next unit if 1 or more upgraded units are unhealthy
        """

        units = self._sorted_units

        partition_ = self._determine_partition(
            units,
            action_event,
        )
        logger.debug(f"{self._partition=}, {partition_=}")
        # Only lower the partition—do not raise it.
        # If this method is called during the action event and then called during another event a
        # few seconds later, `determine_partition()` could return a lower number during the action
        # and then a higher number a few seconds later.
        # This can cause the unit to hang.
        # Example: If partition is lowered to 1, unit 1 begins to upgrade, and partition is set to
        # 2 right away, the unit/Juju agent will hang
        # Details: https://chat.charmhub.io/charmhub/pl/on8rd538ufn4idgod139skkbfr
        # This does not address the situation where another unit > 1 restarts and sets the
        # partition during the `stop` event, but that is unlikely to occur in the small time window
        # that causes the unit to hang.
        if partition_ < self._partition:
            self._partition = partition_
            logger.debug(
                f"Lowered partition to {partition_} {action_event=} {self.in_progress=}"
            )
        if action_event:
            assert len(units) >= 2
            if self._partition > unit_number(units[1]):
                message = "Highest number unit is unhealthy. Refresh will not resume."
                logger.debug(f"Resume refresh event failed: {message}")
                action_event.fail(message)
                return
            # If a unit was unhealthy and the upgrade was forced, only the next unit will
            # upgrade. As long as 1 or more units are unhealthy, the upgrade will need to be
            # forced for each unit.

            # Include "Attempting to" because (on Kubernetes) we only control the partition,
            # not which units upgrade. Kubernetes may not upgrade a unit even if the partition
            # allows it (e.g. if the charm container of a higher unit is not ready). This is
            # also applicable `if not force`, but is unlikely to happen since all units are
            # healthy `if not force`.
            message = f"Attempting to refresh unit {self._partition}."
            action_event.set_results({"result": message})
            logger.debug(f"Resume refresh succeeded: {message}")


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


partition = _Partition()
