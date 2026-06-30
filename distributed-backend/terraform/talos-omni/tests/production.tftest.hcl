mock_provider "kubectl" {}

run "production_plan" {
  command = plan

  variables {
    environment_name      = "eve-trade-ci"
    database_mode         = "external"
    external_database_url = "postgres://runtime:placeholder@database.invalid:5432/eve_trade"
  }

  assert {
    condition     = var.database_mode == "external" && var.external_database_url != ""
    error_message = "the representative Talos/Omni production plan must use an explicit external database"
  }
}
