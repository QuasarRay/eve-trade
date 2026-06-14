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

variable "tags" {
  description = "Tags to apply to ECR repositories."
  type        = map(string)
  default     = {}
}
