resource "google_compute_network" "this" {
  project                 = var.project_id
  name                    = "${var.environment_name}-vpc"
  auto_create_subnetworks = false
  routing_mode            = "REGIONAL"
}

resource "google_compute_subnetwork" "primary" {
  project                  = var.project_id
  name                     = "${var.environment_name}-${var.region}"
  region                   = var.region
  network                  = google_compute_network.this.id
  ip_cidr_range            = var.network_cidr
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "${var.environment_name}-pods"
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = "${var.environment_name}-services"
    ip_cidr_range = var.services_cidr
  }
}

resource "google_compute_router" "nat" {
  project = var.project_id
  name    = "${var.environment_name}-router"
  region  = var.region
  network = google_compute_network.this.id
}

resource "google_compute_router_nat" "nat" {
  project                            = var.project_id
  name                               = "${var.environment_name}-nat"
  router                             = google_compute_router.nat.name
  region                             = google_compute_router.nat.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.primary.id
    source_ip_ranges_to_nat = ["PRIMARY_IP_RANGE", "LIST_OF_SECONDARY_IP_RANGES"]
    secondary_ip_range_names = [
      "${var.environment_name}-pods",
      "${var.environment_name}-services",
    ]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

resource "google_compute_global_address" "private_services" {
  project       = var.project_id
  name          = "google-managed-services-${google_compute_network.this.name}"
  purpose       = "VPC_PEERING"
  address_type  = "INTERNAL"
  prefix_length = var.private_services_prefix_length
  network       = google_compute_network.this.id
}

resource "google_service_networking_connection" "private_services" {
  network                 = google_compute_network.this.id
  service                 = "servicenetworking.googleapis.com"
  reserved_peering_ranges = [google_compute_global_address.private_services.name]
}
