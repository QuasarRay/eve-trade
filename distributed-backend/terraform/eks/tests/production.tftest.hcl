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

  assert {
    condition = (
      aws_db_instance.trade_settlement[0].engine == "postgres" &&
      aws_db_instance.trade_settlement[0].storage_encrypted &&
      !aws_db_instance.trade_settlement[0].publicly_accessible &&
      aws_db_instance.trade_settlement[0].multi_az &&
      aws_db_instance.trade_settlement[0].backup_retention_period >= 7 &&
      aws_db_instance.trade_settlement[0].deletion_protection &&
      !aws_db_instance.trade_settlement[0].skip_final_snapshot
    )
    error_message = "the planned EKS PostgreSQL resource must be encrypted, private, multi-AZ, backed up, and deletion protected"
  }

  assert {
    condition = (
      aws_security_group_rule.trade_settlement_database_ingress[0].from_port == 5432 &&
      aws_security_group_rule.trade_settlement_database_ingress[0].to_port == 5432 &&
      aws_security_group_rule.trade_settlement_database_ingress[0].protocol == "tcp" &&
      aws_security_group_rule.trade_settlement_database_ingress[0].cidr_blocks == null
    )
    error_message = "the EKS database ingress must be TCP/5432 from the worker security group, not a CIDR"
  }

  assert {
    condition     = kubernetes_secret_v1.trade_settlement_database[0].metadata[0].name == "trade-settlement-database"
    error_message = "the EKS plan must wire the runtime database secret expected by application workloads"
  }
}
