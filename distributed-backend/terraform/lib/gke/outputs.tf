output "gke_cluster_id" {
  description = "GKE cluster name."
  value       = google_container_cluster.this.name
}

output "cluster_endpoint" {
  description = "GKE cluster endpoint."
  value       = google_container_cluster.this.endpoint
}

output "cluster_certificate_authority_data" {
  description = "Base64-encoded GKE cluster CA certificate."
  value       = google_container_cluster.this.master_auth[0].cluster_ca_certificate
  sensitive   = true
}

output "cluster_location" {
  description = "GKE cluster location."
  value       = google_container_cluster.this.location
}

output "node_service_account_email" {
  description = "GKE node service account email."
  value       = google_service_account.nodes.email
}

output "cluster_blocker_id" {
  description = "Dependency blocker for cluster creation."
  value       = google_container_cluster.this.id
}

output "addons_blocker_id" {
  description = "Dependency blocker for add-on readiness."
  value       = time_sleep.addons.id
}

output "configure_kubectl" {
  description = "Command to configure kubectl for the GKE cluster."
  value       = "gcloud container clusters get-credentials ${google_container_cluster.this.name} --region ${google_container_cluster.this.location} --project ${var.project_id}"
}
