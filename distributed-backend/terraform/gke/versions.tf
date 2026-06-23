terraform {
  required_version = ">= 1.6.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
    helm = {
      source  = "hashicorp/helm"
      version = "~> 2.17"
    }
    kubectl = {
      source  = "gavinbunney/kubectl"
      version = "~> 1.19"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.37"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.7"
    }
    time = {
      source  = "hashicorp/time"
      version = "~> 0.12"
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
