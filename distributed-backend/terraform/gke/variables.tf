variable "project_id" {
  description = "Google Cloud project ID."
  type        = string
}

variable "environment_name" {
  description = "Environment name used in GCP resource names. Use lowercase letters, numbers, and hyphens."
  type        = string
  default     = "eve-trade-prod"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,40}$", var.environment_name))
    error_message = "environment_name must start with a lowercase letter and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "manage_project_services" {
  description = "Whether Terraform should enable required Google Cloud APIs."
  type        = bool
  default     = true
}

variable "region" {
  description = "Google Cloud region for GKE, Cloud SQL, and Artifact Registry."
  type        = string
  default     = "us-central1"
}

variable "node_locations" {
  description = "Zones used by the regional GKE node pool. Update this when region changes."
  type        = list(string)
  default     = ["us-central1-a", "us-central1-b", "us-central1-c"]
}

variable "network_cidr" {
  description = "Primary subnet CIDR used by GKE nodes."
  type        = string
  default     = "10.20.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary subnet CIDR used by GKE pods."
  type        = string
  default     = "10.21.0.0/16"
}

variable "services_cidr" {
  description = "Secondary subnet CIDR used by GKE services."
  type        = string
  default     = "10.22.0.0/20"
}

variable "private_services_prefix_length" {
  description = "Prefix length reserved for private services access."
  type        = number
  default     = 16
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

  validation {
    condition     = contains(["RAPID", "REGULAR", "STABLE", "EXTENDED", "UNSPECIFIED"], var.release_channel)
    error_message = "release_channel must be one of RAPID, REGULAR, STABLE, EXTENDED, or UNSPECIFIED."
  }
}

variable "cluster_deletion_protection" {
  description = "Whether Terraform should prevent destroying the GKE cluster."
  type        = bool
  default     = true
}

variable "enable_private_endpoint" {
  description = "Whether the GKE control plane endpoint is private-only."
  type        = bool
  default     = true
}

variable "master_ipv4_cidr_block" {
  description = "CIDR block for the private GKE control plane."
  type        = string
  default     = "172.16.0.0/28"
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
  default     = "e2-standard-4"
}

variable "node_disk_size_gb" {
  description = "GKE node boot disk size in GB."
  type        = number
  default     = 80
}

variable "node_service_account_id" {
  description = "Service account ID used by GKE nodes. Must be unique within the project."
  type        = string
  default     = "eve-trade-gke-nodes"
}

variable "initial_node_count" {
  description = "Initial node count per regional node pool location."
  type        = number
  default     = 1
}

variable "min_node_count" {
  description = "Minimum node count per regional node pool location."
  type        = number
  default     = 1
}

variable "max_node_count" {
  description = "Maximum node count per regional node pool location."
  type        = number
  default     = 3
}

variable "artifact_registry_repository_id" {
  description = "Artifact Registry Docker repository ID. Defaults to environment_name."
  type        = string
  default     = null
  nullable    = true
}

variable "container_image_overrides" {
  description = "Optional per-service image overrides keyed by encore-backend, trade-settlement, or quilkin. Each value may include repository and tag."
  type        = map(any)
  default     = {}
}

variable "database_enabled" {
  description = "Whether Terraform should provision the Cloud SQL PostgreSQL database used by trade-settlement."
  type        = bool
  default     = true
}

variable "external_database_url" {
  description = "Existing PostgreSQL URL to place into the Kubernetes database secret when database_enabled is false."
  type        = string
  default     = ""
  sensitive   = true
}

variable "market_database_url" {
  description = "Read-only PostgreSQL URL for the Market service. Terraform stores it in the market-database Secret as MARKET_DATABASE_URL when set."
  type        = string
  default     = ""
  sensitive   = true
}

variable "database_name" {
  description = "PostgreSQL database name."
  type        = string
  default     = "eve_trade"
}

variable "database_username" {
  description = "PostgreSQL application username."
  type        = string
  default     = "eve_trade"
}

variable "database_version" {
  description = "Cloud SQL PostgreSQL database version."
  type        = string
  default     = "POSTGRES_16"
}

variable "database_tier" {
  description = "Cloud SQL instance tier."
  type        = string
  default     = "db-custom-1-3840"
}

variable "database_disk_size_gb" {
  description = "Cloud SQL disk size in GB."
  type        = number
  default     = 20
}

variable "database_availability_type" {
  description = "Cloud SQL availability type."
  type        = string
  default     = "ZONAL"
}

variable "database_backup_enabled" {
  description = "Whether Cloud SQL automated backups are enabled."
  type        = bool
  default     = true
}

variable "database_point_in_time_recovery_enabled" {
  description = "Whether Cloud SQL point-in-time recovery is enabled."
  type        = bool
  default     = true
}

variable "database_deletion_protection" {
  description = "Whether Cloud SQL deletion protection is enabled."
  type        = bool
  default     = true
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

variable "opentelemetry_enabled" {
  description = "Whether Terraform should install the OpenTelemetry Operator and create the GCP instrumentation resource."
  type        = bool
  default     = false
}

variable "opentelemetry_operator_chart_version" {
  description = "Optional OpenTelemetry Operator chart version. Leave null for the chart repository default."
  type        = string
  default     = null
  nullable    = true
}

variable "labels" {
  description = "Additional GCP labels."
  type        = map(string)
  default     = {}
}
