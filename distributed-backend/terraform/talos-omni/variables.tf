variable "environment_name" {
  description = "Environment name used for labels and generated resource names."
  type        = string
  default     = "eve-trade"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,40}$", var.environment_name))
    error_message = "environment_name must start with a lowercase letter and contain only lowercase letters, numbers, and hyphens."
  }
}

variable "omni_cluster_name" {
  description = "Omni cluster name used in helper outputs. Defaults to environment_name."
  type        = string
  default     = null
  nullable    = true
}

variable "kubeconfig_path" {
  description = "Path to a kubeconfig exported from Omni, for example one produced by `omnictl kubeconfig --cluster <name>`. Leave null to let providers use their default kubeconfig discovery."
  type        = string
  default     = null
  nullable    = true
}

variable "kubeconfig_context" {
  description = "Optional kubeconfig context for the Omni-managed Talos cluster."
  type        = string
  default     = null
  nullable    = true
}

variable "app_namespace" {
  description = "Kubernetes namespace for Eve Trade workloads."
  type        = string
  default     = "eve-trade"
}

variable "create_app_namespace" {
  description = "Whether Terraform should create the Eve Trade namespace before Kubernetes manifests are applied."
  type        = bool
  default     = true
}

variable "database_mode" {
  description = "Database preparation mode for Talos/Omni. Use external for a managed or separately operated PostgreSQL, in_cluster for a simple non-production PostgreSQL StatefulSet, or none when another process creates the secret."
  type        = string
  default     = "external"

  validation {
    condition     = contains(["external", "in_cluster", "none"], var.database_mode)
    error_message = "database_mode must be external, in_cluster, or none."
  }
}

variable "external_database_url" {
  description = "Existing PostgreSQL URL to place into the Kubernetes database secret when database_mode is external."
  type        = string
  default     = ""
  sensitive   = true
}

variable "database_name" {
  description = "PostgreSQL database name used by in_cluster mode."
  type        = string
  default     = "eve_trade"
}

variable "database_username" {
  description = "PostgreSQL username used by in_cluster mode."
  type        = string
  default     = "eve_trade"
}

variable "in_cluster_database_password" {
  description = "PostgreSQL password used by in_cluster mode. Required when database_mode is in_cluster."
  type        = string
  default     = ""
  sensitive   = true
}

variable "postgres_image" {
  description = "PostgreSQL container image used by in_cluster mode."
  type        = string
  default     = "postgres:16"
}

variable "postgres_storage_class_name" {
  description = "StorageClass for in_cluster PostgreSQL. Leave null to use the cluster default."
  type        = string
  default     = null
  nullable    = true
}

variable "postgres_storage_size" {
  description = "Persistent volume request size for in_cluster PostgreSQL."
  type        = string
  default     = "20Gi"
}

variable "image_registry" {
  description = "Provider-neutral image registry/repository prefix for Talos/Omni deployments."
  type        = string
  default     = "registry.local/eve-trade"
}

variable "default_image_tag" {
  description = "Default image tag used in Terraform image outputs when no per-service override is supplied."
  type        = string
  default     = "latest"
}

variable "container_image_overrides" {
  description = "Optional per-service image overrides keyed by encore-backend, trade-settlement, or quilkin. Each value may include repository and tag."
  type        = map(any)
  default     = {}
}

variable "opentelemetry_enabled" {
  description = "Whether Terraform should create an OpenTelemetry Instrumentation object for a Talos/Omni cluster that already has the OpenTelemetry Operator CRDs installed."
  type        = bool
  default     = false
}

variable "opentelemetry_instrumentation_namespace" {
  description = "Namespace for the OpenTelemetry Instrumentation object when opentelemetry_enabled is true."
  type        = string
  default     = "opentelemetry-system"
}

variable "opentelemetry_otlp_endpoint" {
  description = "OTLP HTTP endpoint used by the optional OpenTelemetry Instrumentation object."
  type        = string
  default     = "http://otel-collector.eve-trade-observability.svc.cluster.local:4318"
}

variable "labels" {
  description = "Additional labels applied to Terraform-managed Kubernetes resources."
  type        = map(string)
  default     = {}
}
