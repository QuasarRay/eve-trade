variable "environment_name" {
  description = "Name of the environment"
  type        = string
}

variable "cluster_version" {
  description = "EKS cluster version."
  type        = string
  default     = "1.31"
}

variable "cluster_endpoint_public_access" {
  description = "Whether the EKS API endpoint should be publicly reachable."
  type        = bool
  default     = false
}

variable "tags" {
  description = "List of tags to be associated with resources."
  default     = {}
  type        = map(string)
}

variable "vpc_id" {
  description = "VPC ID used to create EKS cluster."
  type        = string
}

variable "vpc_cidr" {
  description = "VPC ID used to create EKS cluster."
  type        = string
}

variable "subnet_ids" {
  description = "List of private subnet IDs used by EKS cluster nodes."
  type        = list(string)
}

variable "opentelemetry_enabled" {
  description = "Boolean value that enables OpenTelemetry."
  type        = bool
  default     = false
}
