#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Manager for handling mongos Kubernetes resources for a single mongos pod."""

import logging
from functools import cached_property
from typing import Literal, NamedTuple
from ops.charm import CharmBase
from lightkube.models.meta_v1 import ObjectMeta
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

AuthMechanism = Literal["SCRAM-SHA-512", "OAUTHBEARER", "SSL"]
AuthProtocol = Literal["SASL_PLAINTEXT", "SASL_SSL", "SSL"]
AuthMap = NamedTuple("AuthMap", protocol=AuthProtocol, mechanism=AuthMechanism)


class NodePortManager:
    """Manager for handling mongos Kubernetes resources for a single mongos pod."""

    def __init__(
        self,
        charm: CharmBase,
        pod_name: str,
        namespace: str,
    ):
        self.charm = charm
        self.pod_name = pod_name
        self.app_name = "-".join(pod_name.split("-")[:-1])
        self.namespace = namespace
        self.nodeport_service_name = f"{self.app_name}-nodeport"
        self.short_auth_mechanism_mapping: dict[AuthMechanism, str] = {
            "SCRAM-SHA-512": "scram",
            "OAUTHBEARER": "oauth",
            "SSL": "ssl",
        }

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
        pod_name = pod_name or self.pod_name

        return self.client.get(
            res=Pod,
            name=self.pod_name,
        )

    def get_service(self, service_name: str) -> Service | None:
        """Gets the Service via the K8s API."""
        return self.client.get(
            res=Service,
            name=service_name,
        )

    def get_node_port(
        self,
        service: Service,
        auth_map: AuthMap,
    ) -> int:
        """Gets the NodePort number for the service via the K8s API."""
        if not service.spec or not service.spec.ports:
            raise Exception("Could not find Service spec or ports")

        for port in service.spec.ports:
            if (
                auth_map.protocol.lower().replace("_", "-") in port.name
                and self.short_auth_mechanism_mapping[auth_map.mechanism] in port.name
            ):
                return port.nodePort

        raise Exception(
            f"Unable to find NodePort using {auth_map.protocol} and {auth_map.mechanism} for the {service} service"
        )

    def build_node_port_services(self) -> Service:
        """Builds a ClusterIP service for initial client connection."""
        pod = self.get_pod(pod_name=self.pod_name)
        if not pod.metadata:
            raise Exception(f"Could not find metadata for {pod}")

        return Service(
            metadata=ObjectMeta(
                name=self.nodeport_service_name,
                namespace=self.namespace,
                # owned by the StatefulSet
                ownerReferences=pod.metadata.ownerReferences,
            ),
            spec=ServiceSpec(
                type="NodePort",
                selector={"app.kubernetes.io/name": self.app_name},
                ports=[
                    ServicePort(
                        protocol="TCP",
                        port=27018,  # TODO map this to our constants file
                        targetPort=27018,  # TODO map this to our constants file
                        name=f"{self.charm.app.name}-nodeport",
                    )
                ],
            ),
        )

    def build_listener_service_name(self, auth_map: AuthMap):
        """Builds the Service name for a given auth.protocol and auth.mechanism.

        Returns:
            String of listener service name
                e.g `mongos-0-sasl-plaintext-scram`, `mongos-12-sasl-ssl-oauth`
        """
        return f"{self.pod_name}-{auth_map.protocol.lower().replace('_','-')}-{self.short_auth_mechanism_mapping[auth_map.mechanism]}"

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


# TODO TLS - common name - SANS - you need each K8s-worker name  !!! Watch out for any changes !!!
# Context: nodeport lets you reach any mongos , then K8s resolves and sends to the correct pod
# if the pod goes away and spins up a new hosts, then we need to issue new certificates
