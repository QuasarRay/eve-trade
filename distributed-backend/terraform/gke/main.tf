locals {
  labels = merge(var.labels, {
    application = "eve-trade"
    environment = var.environment_name
  })

  required_project_services = toset([
    "artifactregistry.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "iam.googleapis.com",
    "servicenetworking.googleapis.com",
    "sqladmin.googleapis.com",
  ])

  artifact_registry_repository_id = coalesce(var.artifact_registry_repository_id, var.environment_name)
}

resource "google_project_service" "required" {
  for_each = var.manage_project_services ? local.required_project_services : toset([])

  project            = var.project_id
  service            = each.key
  disable_on_destroy = false
}

module "network" {
  source = "../lib/gcp-network"

  project_id                     = var.project_id
  environment_name               = var.environment_name
  region                         = var.region
  network_cidr                   = var.network_cidr
  pods_cidr                      = var.pods_cidr
  services_cidr                  = var.services_cidr
  private_services_prefix_length = var.private_services_prefix_length

  depends_on = [
    google_project_service.required
  ]
}

module "_app_gke" {
  source = "../lib/gke"

  providers = {
    kubernetes.addons = kubernetes
    helm.addons       = helm
  }

  project_id                           = var.project_id
  environment_name                     = var.environment_name
  region                               = var.region
  node_locations                       = var.node_locations
  cluster_version                      = var.cluster_version
  release_channel                      = var.release_channel
  network_id                           = module.network.network_id
  subnetwork_id                        = module.network.subnetwork_id
  pods_secondary_range_name            = module.network.pods_secondary_range_name
  services_secondary_range_name        = module.network.services_secondary_range_name
  cluster_deletion_protection          = var.cluster_deletion_protection
  enable_private_endpoint              = var.enable_private_endpoint
  master_ipv4_cidr_block               = var.master_ipv4_cidr_block
  master_authorized_networks           = var.master_authorized_networks
  node_machine_type                    = var.node_machine_type
  node_disk_size_gb                    = var.node_disk_size_gb
  node_service_account_id              = var.node_service_account_id
  initial_node_count                   = var.initial_node_count
  min_node_count                       = var.min_node_count
  max_node_count                       = var.max_node_count
  labels                               = local.labels
  cert_manager_enabled                 = var.cert_manager_enabled
  cert_manager_chart_version           = var.cert_manager_chart_version
  opentelemetry_operator_enabled       = var.opentelemetry_enabled
  opentelemetry_operator_chart_version = var.opentelemetry_operator_chart_version

  depends_on = [
    module.network
  ]
}
