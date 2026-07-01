# Terraform's generated computed values are schema-shaped but do not satisfy every
# AWS provider format validator, so production dependencies use deterministic IDs.
mock_provider "aws" {
  mock_resource "aws_vpc" {
    defaults = {
      id  = "vpc-0123456789abcdef0"
      arn = "arn:aws:ec2:us-east-1:123456789012:vpc/vpc-0123456789abcdef0"
    }
  }

  mock_resource "aws_subnet" {
    defaults = {
      id  = "subnet-0123456789abcdef0"
      arn = "arn:aws:ec2:us-east-1:123456789012:subnet/subnet-0123456789abcdef0"
    }
  }

  mock_resource "aws_security_group" {
    defaults = {
      id  = "sg-0123456789abcdef0"
      arn = "arn:aws:ec2:us-east-1:123456789012:security-group/sg-0123456789abcdef0"
    }
  }

  mock_resource "aws_iam_role" {
    defaults = {
      arn = "arn:aws:iam::123456789012:role/eve-trade-ci"
    }
  }

  mock_resource "aws_iam_policy" {
    defaults = {
      arn = "arn:aws:iam::123456789012:policy/eve-trade-ci"
    }
  }

  mock_resource "aws_launch_template" {
    defaults = {
      id = "lt-0123456789abcdef0"
    }
  }

  mock_resource "aws_eks_cluster" {
    defaults = {
      endpoint = "https://eks.example.test"
      certificate_authority = [{
        data = "dGVzdA=="
      }]
      identity = [{
        oidc = [{
          issuer = "https://oidc.eks.us-east-1.amazonaws.com/id/EXAMPLE"
        }]
      }]
    }
  }

  mock_data "aws_availability_zones" {
    defaults = {
      names = ["us-east-1a", "us-east-1b", "us-east-1c"]
    }
  }

  mock_data "aws_caller_identity" {
    defaults = {
      account_id = "123456789012"
      arn        = "arn:aws:iam::123456789012:user/eve-trade-ci"
      user_id    = "AIDACKCEVSQ6C2EXAMPLE"
    }
  }

  mock_data "aws_iam_policy_document" {
    defaults = {
      json          = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
      minified_json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}"
    }
  }

  mock_data "aws_partition" {
    defaults = {
      partition  = "aws"
      dns_suffix = "amazonaws.com"
    }
  }

  mock_data "aws_region" {
    defaults = {
      name = "us-east-1"
    }
  }
}
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
