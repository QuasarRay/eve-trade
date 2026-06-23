variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "environment_name" {
  description = "Environment name used in GCP resource names."
  type        = string
}

variable "region" {
  description = "Google Cloud region."
  type        = string
}

variable "network_cidr" {
  description = "Primary VPC subnet CIDR used by GKE nodes."
  type        = string
}

variable "pods_cidr" {
  description = "Secondary subnet CIDR used by GKE pods."
  type        = string
}

variable "services_cidr" {
  description = "Secondary subnet CIDR used by GKE services."
  type        = string
}

variable "private_services_prefix_length" {
  description = "Prefix length reserved for private services access."
  type        = number
  default     = 16
}
