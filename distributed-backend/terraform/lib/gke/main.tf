resource "google_service_account" "nodes" {
  project      = var.project_id
  account_id   = var.node_service_account_id
  display_name = "${var.environment_name} GKE node service account"
}

resource "google_project_iam_member" "nodes_artifact_registry" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_monitoring_metric_writer" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "nodes_monitoring_viewer" {
  project = var.project_id
  role    = "roles/monitoring.viewer"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_cluster" "this" {
  project                  = var.project_id
  name                     = var.environment_name
  location                 = var.region
  node_locations           = var.node_locations
  min_master_version       = var.cluster_version
  remove_default_node_pool = true
  initial_node_count       = 1
  network                  = var.network_id
  subnetwork               = var.subnetwork_id
  networking_mode          = "VPC_NATIVE"
  deletion_protection      = var.cluster_deletion_protection
  resource_labels          = var.labels

  release_channel {
    channel = var.release_channel
  }

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_secondary_range_name
    services_secondary_range_name = var.services_secondary_range_name
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = var.enable_private_endpoint
    master_ipv4_cidr_block  = var.master_ipv4_cidr_block
  }

  # Disable legacy client-certificate authentication. Workloads and operators use
  # Google credentials and Workload Identity instead of long-lived client keys.
  master_auth {
    client_certificate_config {
      issue_client_certificate = false
    }
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_networks

      content {
        cidr_block   = cidr_blocks.value.cidr_block
        display_name = cidr_blocks.value.display_name
      }
    }
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  addons_config {
    horizontal_pod_autoscaling {
      disabled = false
    }

    http_load_balancing {
      disabled = false
    }

    network_policy_config {
      disabled = false
    }
  }

  network_policy {
    enabled  = true
    provider = "CALICO"
  }

  logging_service    = "logging.googleapis.com/kubernetes"
  monitoring_service = "monitoring.googleapis.com/kubernetes"

  maintenance_policy {
    recurring_window {
      start_time = "2026-01-01T03:00:00Z"
      end_time   = "2026-01-01T07:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SU"
    }
  }

  lifecycle {
    precondition {
      condition     = var.enable_private_endpoint || length(var.master_authorized_networks) > 0
      error_message = "A public GKE control-plane endpoint requires at least one authorized network."
    }
  }
}

resource "google_container_node_pool" "primary" {
  project    = var.project_id
  name       = "primary"
  location   = google_container_cluster.this.location
  cluster    = google_container_cluster.this.name
  node_count = var.initial_node_count

  autoscaling {
    min_node_count = var.min_node_count
    max_node_count = var.max_node_count
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  upgrade_settings {
    max_surge       = 1
    max_unavailable = 0
  }

  node_config {
    machine_type    = var.node_machine_type
    disk_size_gb    = var.node_disk_size_gb
    disk_type       = "pd-balanced"
    image_type      = "COS_CONTAINERD"
    labels          = var.labels
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    tags            = ["${var.environment_name}-gke-node"]

    metadata = {
      disable-legacy-endpoints = "true"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }

  lifecycle {
    ignore_changes = [node_count]
  }

  depends_on = [
    google_project_iam_member.nodes_artifact_registry,
    google_project_iam_member.nodes_logging,
    google_project_iam_member.nodes_monitoring_metric_writer,
    google_project_iam_member.nodes_monitoring_viewer,
  ]
}

resource "time_sleep" "workloads" {
  create_duration  = "30s"
  destroy_duration = "60s"

  depends_on = [
    google_container_node_pool.primary
  ]
}

resource "kubernetes_namespace_v1" "cert_manager" {
  count    = var.cert_manager_enabled ? 1 : 0
  provider = kubernetes.addons

  metadata {
    name = "cert-manager"

    labels = {
      "app.kubernetes.io/name"       = "cert-manager"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  depends_on = [
    time_sleep.workloads
  ]
}

resource "helm_release" "cert_manager" {
  count    = var.cert_manager_enabled ? 1 : 0
  provider = helm.addons

  name       = "cert-manager"
  repository = "https://charts.jetstack.io"
  chart      = "cert-manager"
  version    = var.cert_manager_chart_version
  namespace  = kubernetes_namespace_v1.cert_manager[0].metadata[0].name
  atomic     = true
  wait       = true
  timeout    = 600

  set {
    name  = "crds.enabled"
    value = "true"
  }
}

resource "kubernetes_namespace_v1" "opentelemetry_system" {
  count    = var.opentelemetry_operator_enabled ? 1 : 0
  provider = kubernetes.addons

  metadata {
    name = "opentelemetry-system"

    labels = {
      "app.kubernetes.io/name"       = "opentelemetry-operator"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  depends_on = [
    time_sleep.workloads
  ]
}

resource "helm_release" "opentelemetry_operator" {
  count    = var.opentelemetry_operator_enabled ? 1 : 0
  provider = helm.addons

  name       = "opentelemetry-operator"
  repository = "https://open-telemetry.github.io/opentelemetry-helm-charts"
  chart      = "opentelemetry-operator"
  version    = var.opentelemetry_operator_chart_version
  namespace  = kubernetes_namespace_v1.opentelemetry_system[0].metadata[0].name
  atomic     = true
  wait       = true
  timeout    = 600

  depends_on = [
    helm_release.cert_manager
  ]
}

resource "time_sleep" "addons" {
  create_duration  = "30s"
  destroy_duration = "60s"

  depends_on = [
    helm_release.cert_manager,
    helm_release.opentelemetry_operator,
  ]
}
