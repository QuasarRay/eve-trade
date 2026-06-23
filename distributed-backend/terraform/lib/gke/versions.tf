terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    helm = {
      source                = "hashicorp/helm"
      version               = "~> 2.17"
      configuration_aliases = [helm.addons]
    }
    kubernetes = {
      source                = "hashicorp/kubernetes"
      version               = "~> 2.37"
      configuration_aliases = [kubernetes.addons]
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }
}
