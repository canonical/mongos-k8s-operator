#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling mongos Kubernetes resources for a single mongos pod."""

import logging
from functools import cached_property
from ops.charm import CharmBase
from lightkube.models.meta_v1 import ObjectMeta, OwnerReference
from lightkube.core.client import Client
from lightkube.core.exceptions import ApiError
from lightkube.resources.core_v1 import Pod, Service
from lightkube.models.core_v1 import ServicePort, ServiceSpec


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

    # --- GETTERS ---

    def get_pod(self, pod_name: str = "") -> Pod:
        """Gets the Pod via the K8s API."""
        # Allows us to get pods from other peer units
        return self.client.get(
            res=Pod,
            name=pod_name or self.pod_name,
        )

    def build_node_port_services(self, port: str) -> Service:
        """Builds a ClusterIP service for initial client connection."""
        pod = self.get_pod(pod_name=self.pod_name)
        if not pod.metadata:
            raise Exception(f"Could not find metadata for {pod}")

        unit_id = self.charm.unit.name.split("/")[1]
        return Service(
            metadata=ObjectMeta(
                name=f"{self.app_name}-{unit_id}-external",
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
                    "app.kubernetes.io/name": self.app_name,
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
