# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling Mongos in-place upgrades."""

import abc
import copy
import enum
import json
import logging
import pathlib
import secrets
import string
import poetry.core.constraints.version as poetry_version
from typing import Dict, List, Tuple, Optional

from ops import BlockedStatus, MaintenanceStatus, StatusBase, Unit
from ops.charm import CharmBase
from ops.framework import Object
from tenacity import retry, stop_after_attempt, wait_fixed

from charms.mongodb.v1.mongos import (
    MongosConnection,
)


# The unique Charmhub library identifier, never change it
LIBID = "0ceb80b02714471bb72a467fb5aa9243"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2

logger = logging.getLogger(__name__)


WRITE_KEY = "write_value"
ROLLBACK_INSTRUCTIONS = "To rollback, `juju refresh` to the previous revision"

SHARD_NAME_INDEX = "_id"

SHARD = "shard"
PEER_RELATION_ENDPOINT_NAME = "upgrade-version-a"
PRECHECK_ACTION_NAME = "pre-upgrade-check"


# BEGIN: Helper functions
def unit_number(unit_: Unit) -> int:
    """Get unit number."""
    return int(unit_.name.split("/")[-1])


# END: Helper functions


# BEGIN: Exceptions
class StatusException(Exception):
    """Exception with ops status."""

    def __init__(self, status: StatusBase) -> None:
        super().__init__(status.message)
        self.status = status


class PrecheckFailed(StatusException):
    """App is not ready to upgrade."""

    def __init__(self, message: str):
        self.message = message
        super().__init__(
            BlockedStatus(
                f"Rollback with `juju refresh`. Pre-upgrade check failed: {self.message}"
            )
        )


class FailedToElectNewPrimaryError(Exception):
    """Raised when a new primary isn't elected after stepping down."""


class ClusterNotHealthyError(Exception):
    """Raised when the cluster is not healthy."""


class BalancerStillRunningError(Exception):
    """Raised when the balancer is still running after stopping it."""


class PeerRelationNotReady(Exception):
    """Upgrade peer relation not available (to this unit)."""


# END: Exceptions


class UnitState(str, enum.Enum):
    """Unit upgrade state."""

    HEALTHY = "healthy"
    RESTARTING = "restarting"  # Kubernetes only
    UPGRADING = "upgrading"  # Machines only
    OUTDATED = "outdated"  # Machines only


# BEGIN: Useful classes
class AbstractUpgrade(abc.ABC):
    """In-place upgrades abstract class (typing).

    Based off specification: DA058 - In-Place Upgrades - Kubernetes v2
    (https://docs.google.com/document/d/1tLjknwHudjcHs42nzPVBNkHs98XxAOT2BXGGpP7NyEU/)
    """

    def __init__(self, charm_: CharmBase) -> None:
        relations = charm_.model.relations[PEER_RELATION_ENDPOINT_NAME]
        if not relations:
            raise PeerRelationNotReady
        assert len(relations) == 1
        self._peer_relation = relations[0]
        self._charm = charm_
        self._unit: Unit = charm_.unit
        self._unit_databag = self._peer_relation.data[self._unit]
        self._app_databag = self._peer_relation.data[charm_.app]
        self._app_name = charm_.app.name
        self._current_versions = {}  # For this unit
        for version, file_name in {
            "charm": "charm_version",
            "workload": "workload_version",
        }.items():
            self._current_versions[version] = (
                pathlib.Path(file_name).read_text().strip()
            )

    @property
    def unit_state(self) -> Optional[UnitState]:
        """Unit upgrade state."""
        if state := self._unit_databag.get("state"):
            return UnitState(state)

    @unit_state.setter
    def unit_state(self, value: UnitState) -> None:
        self._unit_databag["state"] = value.value

    @property
    def is_compatible(self) -> bool:
        """Whether upgrade is supported from previous versions."""
        assert self.versions_set
        try:
            previous_version_strs: Dict[str, str] = json.loads(
                self._app_databag["versions"]
            )
        except KeyError as exception:
            logger.debug("`versions` missing from peer relation", exc_info=exception)
            return False
        # TODO charm versioning: remove `.split("+")` (which removes git hash before comparing)
        previous_version_strs["charm"] = previous_version_strs["charm"].split("+")[0]
        previous_versions: Dict[str, poetry_version.Version] = {
            key: poetry_version.Version.parse(value)
            for key, value in previous_version_strs.items()
        }
        current_version_strs = copy.copy(self._current_versions)
        current_version_strs["charm"] = current_version_strs["charm"].split("+")[0]
        current_versions = {
            key: poetry_version.Version.parse(value)
            for key, value in current_version_strs.items()
        }
        try:
            # TODO Future PR: change this > sign to support downgrades
            if (
                previous_versions["charm"] > current_versions["charm"]
                or previous_versions["charm"].major != current_versions["charm"].major
            ):
                logger.debug(
                    f'{previous_versions["charm"]=} incompatible with {current_versions["charm"]=}'
                )
                return False
            if (
                previous_versions["workload"] > current_versions["workload"]
                or previous_versions["workload"].major
                != current_versions["workload"].major
            ):
                logger.debug(
                    f'{previous_versions["workload"]=} incompatible with {current_versions["workload"]=}'
                )
                return False
            logger.debug(
                f"Versions before upgrade compatible with versions after upgrade {previous_version_strs=} {self._current_versions=}"
            )
            return True
        except KeyError as exception:
            logger.debug(
                f"Version missing from {previous_versions=}", exc_info=exception
            )
            return False

    @property
    def in_progress(self) -> bool:
        """Whether upgrade is in progress."""
        logger.debug(
            f"{self._app_workload_container_version=} {self._unit_workload_container_versions=}"
        )
        return any(
            version != self._app_workload_container_version
            for version in self._unit_workload_container_versions.values()
        )

    @property
    def _sorted_units(self) -> List[Unit]:
        """Units sorted from highest to lowest unit number."""
        return sorted(
            (self._unit, *self._peer_relation.units), key=unit_number, reverse=True
        )

    @abc.abstractmethod
    def _get_unit_healthy_status(self) -> StatusBase:
        """Status shown during upgrade if unit is healthy."""

    def get_unit_juju_status(self) -> Optional[StatusBase]:
        """Unit upgrade status."""
        if self.in_progress:
            return self._get_unit_healthy_status()

    @property
    def app_status(self) -> Optional[StatusBase]:
        """App upgrade status."""
        if not self.in_progress:
            return

        return MaintenanceStatus(
            "Upgrading. To rollback, `juju refresh` to the previous revision"
        )

    @property
    def versions_set(self) -> bool:
        """Whether versions have been saved in app databag.

        Should only be `False` during first charm install.

        If a user upgrades from a charm that does not set versions, this charm will get stuck.
        """
        return self._app_databag.get("versions") is not None

    def set_versions_in_app_databag(self) -> None:
        """Save current versions in app databag.

        Used after next upgrade to check compatibility (i.e. whether that upgrade should be
        allowed).
        """
        assert not self.in_progress
        logger.debug(
            f"Setting {self._current_versions=} in upgrade peer relation app databag"
        )
        self._app_databag["versions"] = json.dumps(self._current_versions)
        logger.debug(
            f"Set {self._current_versions=} in upgrade peer relation app databag"
        )

    @property
    @abc.abstractmethod
    def _unit_workload_container_versions(self) -> Dict[str, str]:
        """{Unit name: unique identifier for unit's workload container version}.

        If and only if this version changes, the workload will restart (during upgrade or
        rollback).

        On Kubernetes, the workload & charm are upgraded together
        On machines, the charm is upgraded before the workload

        This identifier should be comparable to `_app_workload_container_version` to determine if
        the unit & app are the same workload container version.
        """

    @property
    @abc.abstractmethod
    def _app_workload_container_version(self) -> str:
        """Unique identifier for the app's workload container version.

        This should match the workload version in the current Juju app charm version.

        This identifier should be comparable to `_unit_workload_container_versions` to determine if
        the app & unit are the same workload container version.
        """

    @property
    @abc.abstractmethod
    def authorized(self) -> bool:
        """Whether this unit is authorized to upgrade.

        Only applies to machine charm
        """

    @abc.abstractmethod
    def upgrade_unit(self, *, charm) -> None:
        """Upgrade this unit.

        Only applies to machine charm
        """

    def pre_upgrade_check(self) -> None:
        """Check if this app is ready to upgrade.

        Runs before any units are upgraded

        Does *not* run during rollback

        On machines, this runs before any units are upgraded (after `juju refresh`)
        On machines & Kubernetes, this also runs during pre-upgrade-check action

        Can run on leader or non-leader unit

        Raises:
            PrecheckFailed: App is not ready to upgrade

        TODO Kubernetes: Run (some) checks after `juju refresh` (in case user forgets to run
        pre-upgrade-check action). Note: 1 unit will upgrade before we can run checks (checks may
        need to be modified).
        See https://chat.canonical.com/canonical/pl/cmf6uhm1rp8b7k8gkjkdsj4mya
        """
        # Until the mongos charm has a config-server there is nothing to check. Allow an upgrade.
        if not self.charm.mongos_initialised:
            return

        if not self.is_mongos_able_to_read_write():
            raise PrecheckFailed("mongos is not able to read/write.")


class GenericMongosUpgrade(Object, abc.ABC):
    """Substrate agnostif, abstract handler for upgrade events."""

    def __init__(self, charm: CharmBase, *args, **kwargs):
        super().__init__(charm, *args, **kwargs)
        self._observe_events(charm)

    @abc.abstractmethod
    def _observe_events(self, charm: CharmBase) -> None:
        """Handler that should register all event observers."""
        raise NotImplementedError()

    @property
    @abc.abstractmethod
    def _upgrade(self) -> AbstractUpgrade | None:
        raise NotImplementedError()

    def is_mongos_able_to_read_write(self) -> bool:
        """Returns True if mongos is able to read and write."""
        collection_name, write_value = self.get_random_write_and_collection()
        logger.debug("-----\add_write_to_sharded_cluster\n----")
        self.add_write_to_sharded_cluster(collection_name, write_value)

        logger.debug("-----\nchecking write \n----")
        write_replicated = self.confirm_excepted_write_cluster(
            collection_name,
            write_value,
        )

        self.clear_tmp_collection(collection_name)
        if not write_replicated:
            logger.debug("Test read/write to cluster failed.")
            return False

        return True

    def get_random_write_and_collection(self) -> Tuple[str, str]:
        """Returns a tuple for a random collection name and a unique write to add to it."""
        choices = string.ascii_letters + string.digits
        collection_name = "collection_" + "".join(
            [secrets.choice(choices) for _ in range(32)]
        )
        write_value = "unique_write_" + "".join(
            [secrets.choice(choices) for _ in range(16)]
        )
        return (collection_name, write_value)

    def add_write_to_sharded_cluster(self, collection_name, write_value) -> None:
        """Adds a the provided write to the provided database with the provided collection."""
        with MongosConnection(self.charm.mongos_config) as mongos:
            db = mongos.client[self.charm.database]
            test_collection = db[collection_name]
            write = {WRITE_KEY: write_value}
            test_collection.insert_one(write)

    @retry(
        stop=stop_after_attempt(10),
        wait=wait_fixed(1),
        reraise=True,
    )
    def confirm_excepted_write_cluster(
        self,
        collection_name: str,
        expected_write_value: str,
    ) -> None:
        """Returns True if the replica contains the expected write in the provided collection."""
        with MongosConnection(self.charm.mongos_config) as mongos:
            db = mongos.client[self.charm.database]
            test_collection = db[collection_name]
            query = test_collection.find({}, {WRITE_KEY: 1})
            if query[0][WRITE_KEY] != expected_write_value:
                return False

        return True

    def clear_tmp_collection(self, collection_name: str) -> None:
        """Clears the temporary collection."""
        with MongosConnection(self.charm.mongos_config) as mongos:
            db = mongos.client[self.charm.database]
            db.drop_collection(collection_name)


# END: Useful classes
