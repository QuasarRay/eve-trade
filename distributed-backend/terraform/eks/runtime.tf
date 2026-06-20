locals {
  app_namespace = "eve-trade"
  database_port = 5432

  database_url = var.database_enabled ? "postgres://${var.database_username}:${random_password.database[0].result}@${aws_db_instance.trade_settlement[0].address}:${local.database_port}/${var.database_name}" : var.external_database_url
}

resource "random_password" "database" {
  count = var.database_enabled ? 1 : 0

  length  = 32
  special = false
}

resource "aws_db_subnet_group" "trade_settlement" {
  count = var.database_enabled ? 1 : 0

  name       = "${var.environment_name}-trade-settlement"
  subnet_ids = module.vpc.inner.private_subnets

  tags = merge(module.tags.result, {
    Name = "${var.environment_name}-trade-settlement"
  })
}

resource "aws_security_group" "trade_settlement_database" {
  count = var.database_enabled ? 1 : 0

  name        = "${var.environment_name}-trade-settlement-db"
  description = "Allow trade-settlement pods to reach PostgreSQL."
  vpc_id      = module.vpc.inner.vpc_id

  tags = merge(module.tags.result, {
    Name = "${var.environment_name}-trade-settlement-db"
  })
}

resource "aws_security_group_rule" "trade_settlement_database_ingress" {
  count = var.database_enabled ? 1 : 0

  type                     = "ingress"
  description              = "PostgreSQL from EKS worker nodes"
  from_port                = local.database_port
  to_port                  = local.database_port
  protocol                 = "tcp"
  source_security_group_id = module._app_eks.node_security_group_id
  security_group_id        = aws_security_group.trade_settlement_database[0].id
}

resource "aws_db_instance" "trade_settlement" {
  count = var.database_enabled ? 1 : 0

  identifier              = "${var.environment_name}-trade-settlement"
  engine                  = "postgres"
  engine_version          = var.database_engine_version
  instance_class          = var.database_instance_class
  allocated_storage       = var.database_allocated_storage
  storage_type            = "gp3"
  storage_encrypted       = true
  db_name                 = var.database_name
  username                = var.database_username
  password                = random_password.database[0].result
  db_subnet_group_name    = aws_db_subnet_group.trade_settlement[0].name
  vpc_security_group_ids  = [aws_security_group.trade_settlement_database[0].id]
  publicly_accessible     = false
  backup_retention_period = var.database_backup_retention_period
  deletion_protection     = var.database_deletion_protection
  skip_final_snapshot     = !var.database_deletion_protection

  tags = merge(module.tags.result, {
    Name = "${var.environment_name}-trade-settlement"
  })
}

resource "kubernetes_namespace_v1" "app" {
  metadata {
    name = local.app_namespace

    labels = {
      "app.kubernetes.io/name"       = "eve-trade"
      "app.kubernetes.io/part-of"    = "eve-trade"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  depends_on = [
    time_sleep.workloads
  ]
}

resource "kubernetes_secret_v1" "trade_settlement_database" {
  count = var.database_enabled || var.external_database_url != "" ? 1 : 0

  metadata {
    name      = "trade-settlement-database"
    namespace = kubernetes_namespace_v1.app.metadata[0].name

    labels = {
      "app.kubernetes.io/name"       = "trade-settlement"
      "app.kubernetes.io/component"  = "settlement"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  data = {
    DATABASE_URL = local.database_url
  }

  type = "Opaque"
}
