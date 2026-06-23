locals {
  kubeconfig = yamlencode({
    apiVersion      = "v1"
    kind            = "Config"
    current-context = "terraform"
    clusters = [{
      name = module._app_gke.gke_cluster_id
      cluster = {
        certificate-authority-data = module._app_gke.cluster_certificate_authority_data
        server                     = "https://${module._app_gke.cluster_endpoint}"
      }
    }]
    contexts = [{
      name = "terraform"
      context = {
        cluster = module._app_gke.gke_cluster_id
        user    = "terraform"
      }
    }]
    users = [{
      name = "terraform"
      user = {
        token = data.google_client_config.this.access_token
      }
    }]
  })
}

module "container_images" {
  source = "../lib/gcp-images"

  project_id                = var.project_id
  location                  = var.region
  repository_id             = local.artifact_registry_repository_id
  environment_name          = var.environment_name
  container_image_overrides = var.container_image_overrides
  labels                    = local.labels

  depends_on = [
    google_project_service.required
  ]
}

resource "null_resource" "cluster_blocker" {
  triggers = {
    "blocker" = module._app_gke.cluster_blocker_id
  }
}

resource "null_resource" "addons_blocker" {
  triggers = {
    "blocker" = module._app_gke.addons_blocker_id
  }
}

resource "time_sleep" "workloads" {
  create_duration  = "30s"
  destroy_duration = "60s"

  depends_on = [
    null_resource.addons_blocker
  ]
}
