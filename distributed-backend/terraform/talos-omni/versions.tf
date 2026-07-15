terraform {
  # Terraform test was introduced in 1.6; use the same minimum as the mocked
  # cloud roots to keep one supported verification baseline.
  required_version = ">= 1.7.0"

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
