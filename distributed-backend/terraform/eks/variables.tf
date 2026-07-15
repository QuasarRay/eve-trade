variable "environment_name" {
  description = "Name of the environment"
  type        = string
  default     = "eve-trade"
}

variable "opentelemetry_enabled" {
  description = "Boolean value that enables OpenTelemetry."
  type        = bool
  default     = false
}

variable "cluster_version" {
  description = "EKS cluster version."
  type        = string
  default     = "1.33"
}

variable "cluster_endpoint_public_access" {
  description = "Whether the EKS API endpoint should be publicly reachable."
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

variable "container_image_overrides" {
  description = "Optional per-service image overrides keyed by encore-backend, trade-settlement, or quilkin. Each value may include repository and tag."
  type        = map(any)
  default     = {}
}

variable "database_enabled" {
  description = "Whether Terraform should provision the PostgreSQL database used by trade-settlement."
  type        = bool
  default     = true
}

variable "external_database_url" {
  description = "DATABASE_URL to put in the Kubernetes secret when database_enabled is false."
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
  description = "PostgreSQL database name for trade-settlement."
  type        = string
  default     = "eve_trade"
}

variable "database_username" {
  description = "PostgreSQL username for trade-settlement."
  type        = string
  default     = "evetrade"
}

variable "database_instance_class" {
  description = "RDS instance class for the trade-settlement PostgreSQL database."
  type        = string
  default     = "db.t4g.micro"
}

variable "database_allocated_storage" {
  description = "Allocated database storage in GiB."
  type        = number
  default     = 20
}

variable "database_engine_version" {
  description = "Optional PostgreSQL engine version. Leave null to let AWS choose the default for the selected major engine."
  type        = string
  default     = null
}

variable "database_backup_retention_period" {
  description = "Number of days to retain automated database backups."
  type        = number
  default     = 7
}

variable "database_multi_az" {
  description = "Whether to run the PostgreSQL database as a Multi-AZ RDS instance."
  type        = bool
  default     = true
}

variable "database_deletion_protection" {
  description = "Whether to protect the RDS instance from deletion."
  type        = bool
  default     = true
}
