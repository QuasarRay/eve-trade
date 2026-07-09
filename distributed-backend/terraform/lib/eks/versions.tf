terraform {
  required_version = ">= 1.0.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
    kubernetes = {
      source                = "hashicorp/kubernetes"
      version               = "~> 2.37.0"
      configuration_aliases = [kubernetes.cluster, kubernetes.addons]
    }
    helm = {
      source                = "hashicorp/helm"
      version               = "~> 2.17"
      configuration_aliases = [helm.addons]
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
    }
  }
}
