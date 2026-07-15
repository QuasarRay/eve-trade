terraform {
  # Provider mocking in tests requires Terraform 1.7 or newer.
  required_version = ">= 1.7.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 5.100.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "= 2.37.1"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "= 3.2.0"
    }
    kubectl = {
      source  = "gavinbunney/kubectl"
      version = "= 1.19.0"
    }
    null = {
      source  = "hashicorp/null"
      version = "= 3.2.4"
    }
    random = {
      source  = "hashicorp/random"
      version = "= 3.7.2"
    }
    time = {
      source  = "hashicorp/time"
      version = "= 0.13.1"
    }
  }
}

provider "aws" {
}

provider "kubernetes" {
  host                   = module._app_eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module._app_eks.cluster_certificate_authority_data)
  token                  = data.aws_eks_cluster_auth.this.token
}

provider "kubernetes" {
  alias = "cluster"

  host                   = module._app_eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module._app_eks.cluster_certificate_authority_data)
  token                  = data.aws_eks_cluster_auth.cluster.token
}

provider "kubectl" {
  apply_retry_count      = 10
  host                   = module._app_eks.cluster_endpoint
  cluster_ca_certificate = base64decode(module._app_eks.cluster_certificate_authority_data)
  load_config_file       = false
  token                  = data.aws_eks_cluster_auth.this.token
}

provider "helm" {
  kubernetes = {
    host                   = module._app_eks.cluster_endpoint
    cluster_ca_certificate = base64decode(module._app_eks.cluster_certificate_authority_data)
    token                  = data.aws_eks_cluster_auth.this.token
  }
}

