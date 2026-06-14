variable "environment_name" {
  type        = string
  description = "Name of the environment"
}

output "result" {
  value = {
    environment-name = var.environment_name
  }
  description = "Computed tag results"
}