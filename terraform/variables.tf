# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

variable "app_name" {
  description = "Application name"
  type        = string
  default     = "mongos-k8s"
}

variable "base" {
  description = "Charm base (old name: series)"
  type        = string
  default     = "ubuntu@22.04"

  validation {
    condition     = var.base == "ubuntu@22.04"
    error_message = "The base variable only accepts ubuntu@22.04."
  }
}

variable "channel" {
  description = "Charm channel"
  type        = string
  default     = "6/stable"
}

variable "config" {
  description = "Map of charm configuration options"
  type        = map(string)
  default     = {}
}

variable "constraints" {
  description = "String listing constraints for this application"
  type        = string
  default     = null
}

variable "endpoint_bindings" {
  description = "Map of endpoint bindings"
  type = set(object({
    space    = string
    endpoint = optional(string)
  }))
  default = []
}

variable "model_uuid" {
  description = "Model UUID"
  type        = string
}

variable "revision" {
  description = "Charm revision"
  type        = number
  default     = null
}

variable "units" {
  description = "Unit count"
  type        = number
  default     = 1
}
