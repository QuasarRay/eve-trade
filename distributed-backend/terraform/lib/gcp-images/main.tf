locals {
  service_names = toset([
    "encore-backend",
    "trade-settlement",
    "quilkin",
  ])
}

resource "google_artifact_registry_repository" "service" {
  project       = var.project_id
  location      = var.location
  repository_id = var.repository_id
  description   = "Docker images for ${var.environment_name} Eve Trade services."
  format        = "DOCKER"
  labels        = var.labels

  cleanup_policy_dry_run = false

  docker_config {
    immutable_tags = true
  }

  cleanup_policies {
    id     = "delete-untagged-after-14-days"
    action = "DELETE"

    condition {
      tag_state  = "UNTAGGED"
      older_than = "1209600s"
    }
  }
}

locals {
  registry = "${var.location}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.service.repository_id}"

  images = {
    for name in local.service_names : name => {
      repository = coalesce(try(var.container_image_overrides[name].repository, null), "${local.registry}/${name}")
      tag        = coalesce(try(var.container_image_overrides[name].tag, null), var.default_image_tag)
      image      = "${coalesce(try(var.container_image_overrides[name].repository, null), "${local.registry}/${name}")}:${coalesce(try(var.container_image_overrides[name].tag, null), var.default_image_tag)}"
    }
  }
}
