run "production_plan" {
  command = plan

  variables {
    environment_name      = "eve-trade-ci"
    database_mode         = "external"
    external_database_url = "postgres://runtime:placeholder@database.invalid:5432/eve_trade"
    market_database_url   = "postgres://market_readonly:placeholder@database.invalid:5432/eve_trade"
  }

  assert {
    condition     = var.database_mode == "external" && var.external_database_url != ""
    error_message = "the representative Talos/Omni production plan must use an explicit external database"
  }

  assert {
    condition     = length(kubectl_manifest.postgres_statefulset) == 0 && length(kubectl_manifest.postgres_service) == 0
    error_message = "the Talos/Omni external production plan must not create the non-production in-cluster PostgreSQL topology"
  }

  assert {
    condition = (
      length(kubectl_manifest.trade_settlement_database) == 1 &&
      strcontains(kubectl_manifest.trade_settlement_database[0].yaml_body, "trade-settlement-database") &&
      strcontains(kubectl_manifest.trade_settlement_database[0].yaml_body, nonsensitive(var.external_database_url))
    )
    error_message = "the Talos/Omni plan must deliver the explicit external runtime database URL through the expected secret"
  }

  assert {
    condition = (
      length(kubectl_manifest.market_database) == 1 &&
      strcontains(kubectl_manifest.market_database[0].yaml_body, "market-database") &&
      strcontains(kubectl_manifest.market_database[0].yaml_body, nonsensitive(var.market_database_url))
    )
    error_message = "the Talos/Omni plan must deliver a distinct Market read-only database URL through the expected secret"
  }
}
