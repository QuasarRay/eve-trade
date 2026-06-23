variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "location" {
  description = "Artifact Registry location."
  type        = string
}

variable "repository_id" {
  description = "Artifact Registry Docker repository ID."
  type        = string
}

variable "environment_name" {
  description = "Name of the environment."
  type        = string
}

variable "container_image_overrides" {
  description = "Optional per-service image overrides. Each value may include repository and tag."
  type        = map(any)
  default     = {}
}

variable "default_image_tag" {
  description = "Default image tag for generated image references."
  type        = string
  default     = "prod"
}

variable "labels" {
  description = "Labels to apply to Artifact Registry resources."
  type        = map(string)
  default     = {}
}
