variable "model_uuid" {
  description = "Model UUID"
  type        = string
}

variable "mongos_k8s_name" {
  description = "Name for router"
  type        = string
  default     = "mongos-k8s"
}
