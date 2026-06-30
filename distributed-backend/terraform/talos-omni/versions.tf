terraform {
  required_version = ">= 1.6.0"

  required_providers {
    kubectl = {
      source  = "gavinbunney/kubectl"
      version = "= 1.19.0"
    }
  }
}

provider "kubectl" {
  apply_retry_count = 10
  config_path       = var.kubeconfig_path
  config_context    = var.kubeconfig_context
}
