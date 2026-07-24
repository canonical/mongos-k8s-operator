# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

output "application" {
  description = "Object representing the deployed mongos application."
  value       = juju_application.mongos_k8s
}

output "offers" {
  description = "Map of all offers exposed by the single charm."
  value       = {}
}

output "provides" {
  description = "Provides endpoints."
  value = {
    mongos_proxy = {
      kind     = "endpoint"
      name     = juju_application.mongos_k8s.name
      endpoint = "mongos_proxy"
    }
  }
}

output "requires" {
  description = "Map of all \"requires\" endpoints"
  value = {
    certificates = {
      kind     = "endpoint"
      name     = juju_application.mongos_k8s.name
      endpoint = "certificates"
    }
    cluster = {
      kind     = "endpoint"
      name     = juju_application.mongos_k8s.name
      endpoint = "cluster"
    }
    ldap = {
      kind     = "endpoint"
      name     = juju_application.mongos_k8s.name
      endpoint = "ldap"
    }
    ldap_certificate_transfer = {
      kind     = "endpoint"
      name     = juju_application.mongos_k8s.name
      endpoint = "ldap-certificate-transfer"
    }
  }
}
