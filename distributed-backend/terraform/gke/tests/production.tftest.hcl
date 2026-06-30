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
  }

  assert {
    condition     = var.database_backup_enabled && var.database_point_in_time_recovery_enabled && var.database_deletion_protection
    error_message = "the representative GKE production plan must retain backup, PITR, and deletion protection"
  }
}
