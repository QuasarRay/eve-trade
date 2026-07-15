# Terraform's generated computed values are schema-shaped but do not satisfy every
# Google provider format validator, so production dependencies use deterministic IDs.
mock_provider "google" {
  mock_resource "google_compute_network" {
    defaults = {
      id        = "projects/eve-trade-ci-project/global/networks/eve-trade-ci-vpc"
      self_link = "https://www.googleapis.com/compute/v1/projects/eve-trade-ci-project/global/networks/eve-trade-ci-vpc"
    }
  }

  mock_resource "google_compute_subnetwork" {
    defaults = {
      id        = "projects/eve-trade-ci-project/regions/us-central1/subnetworks/eve-trade-ci-primary"
      self_link = "https://www.googleapis.com/compute/v1/projects/eve-trade-ci-project/regions/us-central1/subnetworks/eve-trade-ci-primary"
    }
  }

  mock_resource "google_container_cluster" {
    defaults = {
      endpoint = "127.0.0.1"
      master_auth = {
        cluster_ca_certificate = "dGVzdA=="
      }
    }
  }
}
mock_provider "kubernetes" {}
mock_provider "helm" {}
mock_provider "kubectl" {}
mock_provider "null" {}
mock_provider "random" {}
mock_provider "time" {}

run "production_plan" {
  command = plan

  variables {
    project_id                              = "eve-trade-ci-project"
    environment_name                        = "eve-trade-ci"
    database_backup_enabled                 = true
    database_point_in_time_recovery_enabled = true
    database_deletion_protection            = true
    database_availability_type              = "REGIONAL"
    cluster_deletion_protection             = true
    market_database_url                     = "postgres://market_readonly:placeholder@database.invalid:5432/eve_trade"
  }

  assert {
    condition     = var.database_backup_enabled && var.database_point_in_time_recovery_enabled && var.database_deletion_protection
    error_message = "the representative GKE production plan must retain backup, PITR, and deletion protection"
  }

  assert {
    condition = (
      google_sql_database_instance.trade_settlement[0].deletion_protection &&
      google_sql_database_instance.trade_settlement[0].settings[0].availability_type == "REGIONAL" &&
      google_sql_database_instance.trade_settlement[0].settings[0].disk_type == "PD_SSD" &&
      google_sql_database_instance.trade_settlement[0].settings[0].disk_autoresize &&
      google_sql_database_instance.trade_settlement[0].settings[0].backup_configuration[0].enabled &&
      google_sql_database_instance.trade_settlement[0].settings[0].backup_configuration[0].point_in_time_recovery_enabled &&
      !google_sql_database_instance.trade_settlement[0].settings[0].ip_configuration[0].ipv4_enabled &&
      google_sql_database_instance.trade_settlement[0].settings[0].ip_configuration[0].ssl_mode == "ENCRYPTED_ONLY"
    )
    error_message = "the planned Cloud SQL instance must be regional, private, TLS-only, SSD-backed, backed up with PITR, and deletion protected"
  }

  assert {
    condition     = var.enable_private_endpoint || length(var.master_authorized_networks) > 0
    error_message = "the GKE control plane must be private or restricted to explicit authorized networks"
  }

  assert {
    condition     = kubernetes_secret_v1.trade_settlement_database[0].metadata[0].name == "trade-settlement-database"
    error_message = "the GKE plan must wire the runtime database secret expected by application workloads"
  }

  assert {
    condition = (
      kubernetes_secret_v1.market_database[0].metadata[0].name == "market-database" &&
      kubernetes_secret_v1.market_database[0].data.MARKET_DATABASE_URL == var.market_database_url
    )
    error_message = "the GKE plan must wire a distinct Market read-only database secret"
  }
}
