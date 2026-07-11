terraform {
  # Provider mocking in tests requires Terraform 1.7 or newer.
  required_version = ">= 1.7.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "= 6.50.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "= 2.17.0"
    }
    kubectl = {
      source  = "gavinbunney/kubectl"
      version = "= 1.19.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "= 2.37.1"
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

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "kubernetes" {
  host                   = "https://${module._app_gke.cluster_endpoint}"
  token                  = data.google_client_config.this.access_token
  cluster_ca_certificate = base64decode(module._app_gke.cluster_certificate_authority_data)
}

provider "kubectl" {
  load_config_file       = false
  host                   = "https://${module._app_gke.cluster_endpoint}"
  token                  = data.google_client_config.this.access_token
  cluster_ca_certificate = base64decode(module._app_gke.cluster_certificate_authority_data)
}

provider "helm" {
  kubernetes {
    host                   = "https://${module._app_gke.cluster_endpoint}"
    token                  = data.google_client_config.this.access_token
    cluster_ca_certificate = base64decode(module._app_gke.cluster_certificate_authority_data)
  }
}
