output "deployment_target" {
  description = "Deployment target represented by this Terraform root."
  value       = local.deployment_target
}

output "configure_kubectl" {
  description = "Command to export a kubeconfig for the Omni-managed Talos cluster."
  value       = "omnictl kubeconfig --cluster ${local.omni_cluster_name}"
}

output "container_images" {
  description = "Provider-neutral container image repositories and tags for application services."
  value       = local.container_images
}

output "database_secret_name" {
  description = "Kubernetes Secret containing DATABASE_URL when Terraform creates it."
  value       = local.create_database_secret ? "trade-settlement-database" : null
}

output "database_mode" {
  description = "Database preparation mode selected for the Talos/Omni deployment."
  value       = var.database_mode
}

output "in_cluster_postgres_service" {
  description = "Cluster DNS name for the optional in-cluster PostgreSQL service."
  value       = var.database_mode == "in_cluster" ? local.in_cluster_database_host : null
}

output "opentelemetry_instrumentation" {
  description = "OpenTelemetry Instrumentation resource when enabled."
  value       = local.opentelemetry_instrumentation
}
