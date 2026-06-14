locals {

  kubeconfig = yamlencode({
    apiVersion      = "v1"
    kind            = "Config"
    current-context = "terraform"
    clusters = [{
      name = module._app_eks.eks_cluster_id
      cluster = {
        certificate-authority-data = module._app_eks.cluster_certificate_authority_data
        server                     = module._app_eks.cluster_endpoint
      }
    }]
    contexts = [{
      name = "terraform"
      context = {
        cluster = module._app_eks.eks_cluster_id
        user    = "terraform"
      }
    }]
    users = [{
      name = "terraform"
      user = {
        token = data.aws_eks_cluster_auth.this.token
      }
    }]
  })
}

module "container_images" {
  source = "../lib/images"

  environment_name          = var.environment_name
  container_image_overrides = var.container_image_overrides
  tags                      = module.tags.result
}

resource "null_resource" "cluster_blocker" {
  triggers = {
    "blocker" = module._app_eks.cluster_blocker_id
  }
}

resource "null_resource" "addons_blocker" {
  triggers = {
    "blocker" = module._app_eks.addons_blocker_id
  }
}

resource "time_sleep" "workloads" {
  create_duration  = "30s"
  destroy_duration = "60s"

  depends_on = [
    null_resource.addons_blocker
  ]
}

# Wait for VPC Resource Controller to attach trunk ENIs to nodes
data "kubernetes_nodes" "vpc_ready_nodes" {
  depends_on = [time_sleep.workloads]

  metadata {
    labels = {
      "vpc.amazonaws.com/has-trunk-attached" = "true"
    }
  }
}
