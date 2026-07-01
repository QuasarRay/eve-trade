mock_provider "google" {}
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
      !google_sql_database_instance.trade_settlement[0].settings[0].ip_configuration[0].ipv4_enabled
    )
    error_message = "the planned Cloud SQL instance must be regional, private, SSD-backed, backed up with PITR, and deletion protected"
  }

  assert {
    condition     = kubernetes_secret_v1.trade_settlement_database[0].metadata[0].name == "trade-settlement-database"
    error_message = "the GKE plan must wire the runtime database secret expected by application workloads"
  }
}
