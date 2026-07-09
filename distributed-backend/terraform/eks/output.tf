output "configure_kubectl" {
  description = "Command to update kubeconfig for this cluster"
  value       = module._app_eks.configure_kubectl
}

output "container_images" {
  description = "ECR-backed container image repositories and tags for application services."
  value       = module.container_images.images
}

output "ecr_repository_urls" {
  description = "ECR repository URLs keyed by application service."
  value       = module.container_images.repository_urls
}

output "database_endpoint" {
  description = "RDS endpoint for trade-settlement when database_enabled is true."
  value       = var.database_enabled ? aws_db_instance.trade_settlement[0].endpoint : null
}

output "database_secret_name" {
  description = "Kubernetes secret containing DATABASE_URL for trade-settlement."
  value       = try(kubernetes_secret_v1.trade_settlement_database[0].metadata[0].name, null)
}

output "market_database_secret_name" {
  description = "Kubernetes secret containing MARKET_DATABASE_URL for Market read-only access."
  value       = try(kubernetes_secret_v1.market_database[0].metadata[0].name, null)
}
