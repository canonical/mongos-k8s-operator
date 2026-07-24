module "mongos_k8s" {
  source     = "../"
  app_name   = var.mongos_k8s_name
  channel    = "8-transition/edge"
  model_uuid = var.model_uuid
}

resource "juju_application" "data-integrator" {
  charm {
    name    = "data-integrator"
    channel = "latest/stable"
  }
  config     = { "database-name" : "test", "extra-user-roles" : "admin" }
  model_uuid = var.model_uuid
}

resource "juju_integration" "mongos_client" {
  model_uuid = module.mongos_k8s.application.model_uuid

  application {
    name     = juju_application.data-integrator.name
    endpoint = "mongodb"
  }
  application {
    name     = module.mongos_k8s.provides["mongos_proxy"].name
    endpoint = module.mongos_k8s.provides["mongos_proxy"].endpoint
  }
  depends_on = [
    juju_application.data-integrator,
    module.mongos_k8s.application
  ]
}
