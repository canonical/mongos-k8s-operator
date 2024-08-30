#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling mongos Kubernetes resources for a single mongos pod."""
from typing import Optional
import logging
from functools import cached_property
from ops.charm import CharmBase
from lightkube.models.meta_v1 import ObjectMeta, OwnerReference
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.core_v1 import Pod, Service, Node
from lightkube.models.core_v1 import ServicePort, ServiceSpec
from ops.model import BlockedStatus

logger = logging.getLogger(__name__)

# default logging from lightkube httpx requests is very noisy
logging.getLogger("lightkube").disabled = True
logging.getLogger("lightkube.core.client").disabled = True
logging.getLogger("httpx").disabled = True
logging.getLogger("httpcore").disabled = True


class NodePortManager:
    """Manager for handling mongos Kubernetes resources for a single mongos pod."""

    def __init__(
        self,
        charm: CharmBase,
    ):
        self.charm = charm
        self.pod_name = self.charm.unit.name.replace("/", "-")
        self.app_name = self.charm.app.name
        self.namespace = self.charm.model.name

    @cached_property
    def client(self) -> Client:
        """The Lightkube client."""
        return Client(  # pyright: ignore[reportArgumentType]
            field_manager=self.pod_name,
            namespace=self.namespace,
        )

    # BEGIN: getters
    def get_service(self, service_name: str) -> Service | None:
        """Gets the Service via the K8s API."""
        return self.client.get(
            res=Service,
            name=service_name,
        )

    def get_pod(self, pod_name: str = "") -> Pod:
        """Gets the Pod via the K8s API."""
        # Allows us to get pods from other peer units
        return self.client.get(
            res=Pod,
            name=pod_name or self.pod_name,
        )

    def get_unit_service_name(self) -> str:
        """Returns the service name for the current unit."""
        unit_id = self.charm.unit.name.split("/")[1]
        return f"{self.app_name}-{unit_id}-external"

    def get_unit_service(self) -> Service | None:
        """Gets the Service via the K8s API for the current unit."""
        return self.get_service(self.get_unit_service_name())

    # END: getters

    # BEGIN: helpers
    def on_deployed_without_trust(self) -> None:
        """Blocks the application and returns a specific error message for deployments made without --trust."""
        self.charm.unit.status = BlockedStatus(
            f"Insufficient permissions, try: `juju trust {self.app.name} --scope=cluster`"
        )

    def build_node_port_services(self, port: str) -> Service:
        """Builds a ClusterIP service for initial client connection."""
        pod = self.get_pod(pod_name=self.pod_name)
        if not pod.metadata:
            raise Exception(f"Could not find metadata for {pod}")

        return Service(
            metadata=ObjectMeta(
                name=self.get_unit_service_name(),
                namespace=self.namespace,
                # When we scale-down K8s will keep the Services for the deleted units around,
                # unless the Services' owner is also deleted.
                ownerReferences=[
                    OwnerReference(
                        apiVersion=pod.apiVersion,
                        kind=pod.kind,
                        name=self.pod_name,
                        uid=pod.metadata.uid,
                        blockOwnerDeletion=False,
                    )
                ],
            ),
            spec=ServiceSpec(
                externalTrafficPolicy="Local",
                type="NodePort",
                selector={
                    "statefulset.kubernetes.io/pod-name": self.pod_name,
                },
                ports=[
                    ServicePort(
                        protocol="TCP",
                        port=port,
                        targetPort=port,
                        name=f"{self.pod_name}-port",
                    )
                ],
            ),
        )

    def apply_service(self, service: Service) -> None:
        """Applies a given Service."""
        try:
            self.client.apply(service)
        except ApiError as e:
            if e.status.code == 403:
                logger.error("Could not apply service, application needs `juju trust`")
                return
            if e.status.code == 422 and "port is already allocated" in e.status.message:
                logger.error(e.status.message)
                return
            else:
                raise

    def delete_unit_service(self) -> None:
        """Deletes a unit Service, if it exists."""
        try:
            service = self.get_unit_service()
        except ApiError as e:
            if e.status.code == 404:
                logger.debug(f"Could not find {self.get_unit_service_name()} to delete.")
                return

        if not service.metadata:
            raise Exception(f"Could not find metadata for {service}")

        try:
            self.client.delete(Service, service.metadata.name)
        except ApiError as e:
            if e.status.code == 403:
                self.on_deployed_without_trust()
                logger.error("Could not delete service, application needs `juju trust`")
                return
            else:
                raise

    @property
    def _node_name(self) -> str:
        """Return the node name for this unit's pod ip."""
        try:
            pod = self.client.get(
                Pod,
                name=self.charm.unit.name.replace("/", "-"),
                namespace=self.namespace,
            )
        except ApiError as e:
            if e.status.code == 403:
                self.on_deployed_without_trust()
                return

        return pod.spec.nodeName

    @property
    def get_node_ip(self) -> Optional[str]:
        """Return node IP."""
        try:
            node = self.client.get(
                Node,
                name=self._node_name,
                namespace=self.namespace,
            )
        except ApiError as e:
            if e.status.code == 403:
                logger.error("Could not delete service, application needs `juju trust`")
                self.on_deployed_without_trust()
                return
        # [
        #    NodeAddress(address='192.168.0.228', type='InternalIP'),
        #    NodeAddress(address='example.com', type='Hostname')
        # ]
        # Remember that OpenStack, for example, will return an internal hostname, which is not
        # accessible from the outside. Give preference to ExternalIP, then InternalIP first
        # Separated, as we want to give preference to ExternalIP, InternalIP and then Hostname
        for typ in ["ExternalIP", "InternalIP", "Hostname"]:
            for a in node.status.addresses:
                if a.type == typ:
                    return a.address

    def get_node_port(self, port_to_match: int) -> int:
        """Return node port for the provided port to match."""
        service = self.get_unit_service()

        if not service or not service.spec.type == "NodePort":
            raise Exception("No service found for port.")

        for svc_port in service.spec.ports:
            if svc_port.port == 27018:
                return svc_port.nodePort

        raise Exception(f"Unable to find NodePort for {port_to_match} for the {service} service")

    # END: helpers
