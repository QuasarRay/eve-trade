locals {
  app_namespace = "eve-trade"
  database_port = 5432

  database_url = var.database_enabled ? "postgres://${var.database_username}:${random_password.database[0].result}@${google_sql_database_instance.trade_settlement[0].private_ip_address}:${local.database_port}/${var.database_name}" : var.external_database_url
}

resource "random_password" "database" {
  count = var.database_enabled ? 1 : 0

  length  = 32
  special = false
}

resource "google_sql_database_instance" "trade_settlement" {
  count = var.database_enabled ? 1 : 0

  project             = var.project_id
  name                = "${var.environment_name}-trade-settlement"
  database_version    = var.database_version
  region              = var.region
  deletion_protection = var.database_deletion_protection

  settings {
    tier              = var.database_tier
    availability_type = var.database_availability_type
    disk_type         = "PD_SSD"
    disk_size         = var.database_disk_size_gb
    disk_autoresize   = true
    user_labels       = local.labels

    backup_configuration {
      enabled                        = var.database_backup_enabled
      point_in_time_recovery_enabled = var.database_point_in_time_recovery_enabled
    }

    ip_configuration {
      ipv4_enabled    = false
      private_network = module.network.network_id
    }
  }

  depends_on = [
    module.network
  ]
}

resource "google_sql_database" "trade_settlement" {
  count = var.database_enabled ? 1 : 0

  project  = var.project_id
  name     = var.database_name
  instance = google_sql_database_instance.trade_settlement[0].name
}

resource "google_sql_user" "trade_settlement" {
  count = var.database_enabled ? 1 : 0

  project  = var.project_id
  name     = var.database_username
  instance = google_sql_database_instance.trade_settlement[0].name
  password = random_password.database[0].result
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

  depends_on = [
    google_sql_database.trade_settlement,
    google_sql_user.trade_settlement,
  ]
}
