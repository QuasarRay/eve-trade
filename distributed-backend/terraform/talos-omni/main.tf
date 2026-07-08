locals {
  deployment_target = "talos-omni"
  omni_cluster_name = coalesce(var.omni_cluster_name, var.environment_name)
  app_namespace     = var.app_namespace

  labels = merge(var.labels, {
    "app.kubernetes.io/part-of"    = "eve-trade"
    "app.kubernetes.io/managed-by" = "terraform"
    "eve-trade.io/deployment"      = local.deployment_target
    "eve-trade.io/environment"     = var.environment_name
  })

  service_image_names = toset([
    "encore-backend",
    "trade-settlement",
    "quilkin",
  ])

  image_registry = trimsuffix(var.image_registry, "/")
  container_images = {
    for name in local.service_image_names : name => {
      repository = coalesce(try(var.container_image_overrides[name].repository, null), "${local.image_registry}/${name}")
      tag        = coalesce(try(var.container_image_overrides[name].tag, null), var.default_image_tag)
      image      = "${coalesce(try(var.container_image_overrides[name].repository, null), "${local.image_registry}/${name}")}:${coalesce(try(var.container_image_overrides[name].tag, null), var.default_image_tag)}"
    }
  }

  in_cluster_database_host = "eve-trade-postgres.${local.app_namespace}.svc.cluster.local"
  database_url = (
    var.database_mode == "in_cluster"
    ? "postgres://${var.database_username}:${var.in_cluster_database_password}@${local.in_cluster_database_host}:5432/${var.database_name}"
    : var.external_database_url
  )
  create_database_secret = var.database_mode != "none"
}

check "in_cluster_database_password" {
  assert {
    condition     = var.database_mode != "in_cluster" || var.in_cluster_database_password != ""
    error_message = "in_cluster_database_password must be set when database_mode is in_cluster."
  }
}

check "external_database_url" {
  assert {
    condition     = var.database_mode != "external" || nonsensitive(var.external_database_url) != ""
    error_message = "external_database_url must be set when database_mode is external."
  }
}

resource "kubectl_manifest" "app_namespace" {
  count = var.create_app_namespace ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "v1"
    kind       = "Namespace"
    metadata = {
      name = local.app_namespace
      labels = merge(local.labels, {
        "app.kubernetes.io/name"             = "eve-trade"
        "gateway.networking.k8s.io/access"   = "public-web"
        "pod-security.kubernetes.io/enforce" = "restricted"
        "pod-security.kubernetes.io/audit"   = "restricted"
        "pod-security.kubernetes.io/warn"    = "restricted"
      })
    }
  })
}

resource "kubectl_manifest" "postgres_auth" {
  count = var.database_mode == "in_cluster" ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "v1"
    kind       = "Secret"
    metadata = {
      name      = "eve-trade-postgres-auth"
      namespace = local.app_namespace
      labels = merge(local.labels, {
        "app.kubernetes.io/name"      = "eve-trade-postgres"
        "app.kubernetes.io/component" = "database"
      })
    }
    stringData = {
      POSTGRES_DB       = var.database_name
      POSTGRES_USER     = var.database_username
      POSTGRES_PASSWORD = var.in_cluster_database_password
    }
    type = "Opaque"
  })

  depends_on = [
    kubectl_manifest.app_namespace,
  ]
}

resource "kubectl_manifest" "trade_settlement_database" {
  count = local.create_database_secret ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "v1"
    kind       = "Secret"
    metadata = {
      name      = "trade-settlement-database"
      namespace = local.app_namespace
      labels = merge(local.labels, {
        "app.kubernetes.io/name"      = "trade-settlement"
        "app.kubernetes.io/component" = "settlement"
      })
    }
    stringData = {
      DATABASE_URL = local.database_url
    }
    type = "Opaque"
  })

  depends_on = [
    kubectl_manifest.app_namespace,
    kubectl_manifest.postgres_statefulset,
  ]
}

resource "kubectl_manifest" "postgres_service" {
  count = var.database_mode == "in_cluster" ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "v1"
    kind       = "Service"
    metadata = {
      name      = "eve-trade-postgres"
      namespace = local.app_namespace
      labels = merge(local.labels, {
        "app.kubernetes.io/name"      = "eve-trade-postgres"
        "app.kubernetes.io/component" = "database"
      })
    }
    spec = {
      type = "ClusterIP"
      selector = {
        "app.kubernetes.io/name" = "eve-trade-postgres"
      }
      ports = [{
        name       = "postgres"
        port       = 5432
        targetPort = 5432
      }]
    }
  })

  depends_on = [
    kubectl_manifest.app_namespace,
  ]
}

resource "kubectl_manifest" "postgres_statefulset" {
  count = var.database_mode == "in_cluster" ? 1 : 0

  yaml_body = yamlencode({
    apiVersion = "apps/v1"
    kind       = "StatefulSet"
    metadata = {
      name      = "eve-trade-postgres"
      namespace = local.app_namespace
      labels = merge(local.labels, {
        "app.kubernetes.io/name"      = "eve-trade-postgres"
        "app.kubernetes.io/component" = "database"
      })
    }
    spec = {
      serviceName = "eve-trade-postgres"
      replicas    = 1
      selector = {
        matchLabels = {
          "app.kubernetes.io/name" = "eve-trade-postgres"
        }
      }
      template = {
        metadata = {
          labels = {
            "app.kubernetes.io/name"      = "eve-trade-postgres"
            "app.kubernetes.io/component" = "database"
          }
        }
        spec = {
          securityContext = {
            fsGroup = 999
          }
          containers = [{
            name  = "postgres"
            image = var.postgres_image
            ports = [{
              name          = "postgres"
              containerPort = 5432
            }]
            envFrom = [{
              secretRef = {
                name = "eve-trade-postgres-auth"
              }
            }]
            volumeMounts = [{
              name      = "data"
              mountPath = "/var/lib/postgresql/data"
            }]
            readinessProbe = {
              exec = {
                command = ["pg_isready", "-U", var.database_username, "-d", var.database_name]
              }
              periodSeconds    = 5
              timeoutSeconds   = 3
              failureThreshold = 12
            }
            livenessProbe = {
              exec = {
                command = ["pg_isready", "-U", var.database_username, "-d", var.database_name]
              }
              initialDelaySeconds = 30
              periodSeconds       = 20
              timeoutSeconds      = 3
              failureThreshold    = 6
            }
            resources = {
              requests = {
                cpu    = "100m"
                memory = "256Mi"
              }
              limits = {
                cpu    = "1000m"
                memory = "1Gi"
              }
            }
          }]
        }
      }
      volumeClaimTemplates = [{
        metadata = {
          name = "data"
        }
        spec = merge(
          {
            accessModes = ["ReadWriteOnce"]
            resources = {
              requests = {
                storage = var.postgres_storage_size
              }
            }
          },
          var.postgres_storage_class_name == null ? {} : {
            storageClassName = var.postgres_storage_class_name
          }
        )
      }]
    }
  })

  depends_on = [
    kubectl_manifest.postgres_auth,
    kubectl_manifest.postgres_service,
  ]
}
