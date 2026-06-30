mock_provider "aws" {}
mock_provider "kubernetes" {}
mock_provider "kubernetes" { alias = "cluster" }
mock_provider "helm" {}
mock_provider "kubectl" {}
mock_provider "null" {}
mock_provider "random" {}
mock_provider "time" {}

run "production_plan" {
  command = plan

  variables {
    environment_name                 = "eve-trade-ci"
    database_multi_az                = true
    database_backup_retention_period = 7
    database_deletion_protection     = true
  }

  assert {
    condition     = var.database_multi_az && var.database_deletion_protection
    error_message = "the representative EKS production plan must retain database HA and deletion protection"
  }
}
