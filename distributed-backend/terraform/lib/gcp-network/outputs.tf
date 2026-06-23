output "network_id" {
  description = "GCP VPC network ID."
  value       = google_compute_network.this.id
}

output "network_name" {
  description = "GCP VPC network name."
  value       = google_compute_network.this.name
}

output "network_self_link" {
  description = "GCP VPC network self link."
  value       = google_compute_network.this.self_link
}

output "subnetwork_id" {
  description = "Primary subnetwork ID."
  value       = google_compute_subnetwork.primary.id
}

output "subnetwork_name" {
  description = "Primary subnetwork name."
  value       = google_compute_subnetwork.primary.name
}

output "pods_secondary_range_name" {
  description = "Secondary range name used for GKE pods."
  value       = "${var.environment_name}-pods"
}

output "services_secondary_range_name" {
  description = "Secondary range name used for GKE services."
  value       = "${var.environment_name}-services"
}

output "private_services_connection_id" {
  description = "Private services access connection ID."
  value       = google_service_networking_connection.private_services.id
}
