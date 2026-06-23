output "configure_kubectl" {
  description = "Command to configure kubectl for the GKE cluster."
  value       = module._app_gke.configure_kubectl
}

output "kubeconfig" {
  description = "Generated kubeconfig for Terraform-created GKE cluster."
  value       = local.kubeconfig
  sensitive   = true
}

output "container_images" {
  description = "Artifact Registry-backed container image repositories and tags for application services."
  value       = module.container_images.images
}

output "artifact_registry" {
  description = "Artifact Registry Docker registry/repository prefix used by CI."
  value       = module.container_images.registry
}

output "artifact_registry_repository_urls" {
  description = "Artifact Registry image repository URLs keyed by application service."
  value       = module.container_images.repository_urls
}

output "database_connection_name" {
  description = "Cloud SQL instance connection name when Terraform provisions the database."
  value       = var.database_enabled ? google_sql_database_instance.trade_settlement[0].connection_name : null
}

output "database_private_ip_address" {
  description = "Cloud SQL private IP address when Terraform provisions the database."
  value       = var.database_enabled ? google_sql_database_instance.trade_settlement[0].private_ip_address : null
  sensitive   = true
}

output "database_secret_name" {
  description = "Kubernetes Secret containing DATABASE_URL when created."
  value       = var.database_enabled || nonsensitive(var.external_database_url) != "" ? "trade-settlement-database" : null
}

output "opentelemetry_instrumentation" {
  description = "OpenTelemetry Instrumentation resource when enabled."
  value       = local.opentelemetry_instrumentation
}
