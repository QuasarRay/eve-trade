variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "environment_name" {
  description = "Environment name used in GKE resource names."
  type        = string
}

variable "region" {
  description = "Regional GKE control plane location."
  type        = string
}

variable "node_locations" {
  description = "Zones used by the regional node pool."
  type        = list(string)
}

variable "cluster_version" {
  description = "Optional GKE minimum master version. Leave null to follow the selected release channel."
  type        = string
  default     = null
  nullable    = true
}

variable "release_channel" {
  description = "GKE release channel."
  type        = string
  default     = "REGULAR"
}

variable "network_id" {
  description = "GCP VPC network ID."
  type        = string
}

variable "subnetwork_id" {
  description = "GCP subnetwork ID."
  type        = string
}

variable "pods_secondary_range_name" {
  description = "Secondary range name used for GKE pods."
  type        = string
}

variable "services_secondary_range_name" {
  description = "Secondary range name used for GKE services."
  type        = string
}

variable "cluster_deletion_protection" {
  description = "Whether Terraform should prevent cluster destruction."
  type        = bool
  default     = true
}

variable "enable_private_endpoint" {
  description = "Whether the GKE control plane endpoint is private-only."
  type        = bool
  default     = false
}

variable "master_ipv4_cidr_block" {
  description = "CIDR block for the private GKE control plane."
  type        = string
}

variable "master_authorized_networks" {
  description = "Optional public control plane authorized networks."
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

variable "node_machine_type" {
  description = "GKE node machine type."
  type        = string
}

variable "node_disk_size_gb" {
  description = "GKE node boot disk size in GB."
  type        = number
}

variable "node_service_account_id" {
  description = "Service account ID used by GKE nodes. Must be unique within the project."
  type        = string
}

variable "initial_node_count" {
  description = "Initial node count per regional node pool location."
  type        = number
}

variable "min_node_count" {
  description = "Minimum node count per regional node pool location."
  type        = number
}

variable "max_node_count" {
  description = "Maximum node count per regional node pool location."
  type        = number
}

variable "labels" {
  description = "Labels to apply to GKE resources."
  type        = map(string)
  default     = {}
}

variable "cert_manager_enabled" {
  description = "Whether to install cert-manager through Helm."
  type        = bool
  default     = true
}

variable "cert_manager_chart_version" {
  description = "Optional cert-manager chart version. Leave null for the chart repository default."
  type        = string
  default     = null
  nullable    = true
}

variable "opentelemetry_operator_enabled" {
  description = "Whether to install the OpenTelemetry Operator through Helm."
  type        = bool
  default     = false
}

variable "opentelemetry_operator_chart_version" {
  description = "Optional OpenTelemetry Operator chart version. Leave null for the chart repository default."
  type        = string
  default     = null
  nullable    = true
}
