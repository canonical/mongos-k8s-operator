module "mongos-k8s" {
  source   = "../"
  app_name = var.mongos_name
  model    = var.model_name
  units    = "1"
  channel  = "6/edge"
}


resource "juju_integration" "data-integrator_mongos-integration" {
  model = var.model_name

  application {
    name = juju_application.data-integrator.name
  }
  application {
    name = var.mongos_name
  }
  depends_on = [
    juju_application.data-integrator,
    module.mongos-k8s
  ]

}


resource "null_resource" "juju_wait_deployment" {
  provisioner "local-exec" {
    command = <<-EOT
    juju-wait -v --model ${var.model_name}
    EOT
  }

}
