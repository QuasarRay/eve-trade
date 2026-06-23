output "images" {
  description = "Image repository, tag, and full image reference keyed by service name."
  value       = local.images
}

output "registry" {
  description = "Artifact Registry Docker registry/repository prefix used by CI."
  value       = local.registry
}

output "repository_id" {
  description = "Artifact Registry repository ID."
  value       = google_artifact_registry_repository.service.repository_id
}

output "repository_name" {
  description = "Artifact Registry repository resource name."
  value       = google_artifact_registry_repository.service.name
}

output "repository_urls" {
  description = "Artifact Registry image repository URLs keyed by application service."
  value       = { for name in local.service_names : name => "${local.registry}/${name}" }
}
