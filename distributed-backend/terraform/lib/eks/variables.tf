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

variable "node_egress_ipv4_cidrs" {
  description = "Explicit IPv4 CIDRs for approved HTTPS egress proxies or private registry endpoints used by EKS nodes."
  type        = list(string)

  validation {
    condition     = length(var.node_egress_ipv4_cidrs) > 0 && alltrue([for cidr in var.node_egress_ipv4_cidrs : can(cidrnetmask(cidr)) && cidr != "0.0.0.0/0"])
    error_message = "node_egress_ipv4_cidrs must contain at least one valid explicit IPv4 CIDR and must not allow 0.0.0.0/0."
  }
}
