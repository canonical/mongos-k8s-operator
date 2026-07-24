# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

resource "juju_application" "mongos_k8s" {
  charm {
    name     = "mongos-k8s"
    channel  = var.channel
    revision = var.revision
    base     = var.base
  }
  config            = var.config
  endpoint_bindings = var.endpoint_bindings
  model_uuid        = var.model_uuid
  name              = var.app_name
  units             = var.units
}

resource "juju_offer" "mongos_proxy" {
  model_uuid       = var.model_uuid
  application_name = juju_application.mongos_k8s.name
  endpoints        = ["mongos_proxy"]
  depends_on       = [juju_application.mongos_k8s]
}
