# Applies the intentionally simple trade_instance rename to the Rust DB layer.
# Run from the repository root.
#
# This script does not add compatibility aliases. It removes the old trade_order naming
# from DB-facing Rust code and points SQL at trade.trade_instance.

$ErrorActionPreference = "Stop"

# This block limits the rename to the trade-settlement DB implementation files.
$files = @(
    "distributed-backend/src/trade-settlement/src/db/orders.rs",
    "distributed-backend/src/trade-settlement/src/db/settlements.rs",
    "distributed-backend/src/trade-settlement/src/db/queries.rs",
    "distributed-backend/src/trade-settlement/src/db/rows.rs",
    "distributed-backend/src/trade-settlement/src/db/idempotency.rs",
    "distributed-backend/src/trade-settlement/src/db/proto_builders.rs",
    "distributed-backend/src/trade-settlement/src/error.rs"
)

# This block applies the aggregate rename consistently in DB-facing Rust code.
foreach ($file in $files) {
    if (-not (Test-Path $file)) {
        Write-Host "Skipping missing file: $file"
        continue
    }

    $text = Get-Content $file -Raw

    # Database table and column names.
    $text = $text.Replace("trade.trade_order", "trade.trade_instance")
    $text = $text.Replace("trade_order_id", "trade_instance_id")
    $text = $text.Replace("offered_item_stack_id", "offered_item")

    # Internal Rust names that are not generated protobuf API names.
    $text = $text.Replace("TradeOrderRow", "TradeInstanceRow")
    $text = $text.Replace("trade order", "trade instance")
    $text = $text.Replace("Trade order", "Trade instance")

    Set-Content $file $text -NoNewline
}

# This block updates item_stack ownership reads after capsuleer_id was renamed to owner_id.
$rowsFile = "distributed-backend/src/trade-settlement/src/db/rows.rs"
if (Test-Path $rowsFile) {
    $text = Get-Content $rowsFile -Raw
    $text = $text.Replace("pub struct ItemStackRow { pub item_stack_id: String, pub capsuleer_id: String,", "pub struct ItemStackRow { pub item_stack_id: String, pub owner_id: String,")
    Set-Content $rowsFile $text -NoNewline
}

$ownershipFile = "distributed-backend/src/trade-settlement/src/db/ownership.rs"
if (Test-Path $ownershipFile) {
    $text = Get-Content $ownershipFile -Raw
    $text = $text.Replace("capsuleer_id::text AS capsuleer_id", "owner_id::text AS owner_id")
    $text = $text.Replace("capsuleer_id,", "owner_id,")
    Set-Content $ownershipFile $text -NoNewline
}

# This block leaves generated protobuf-facing names alone if they still exist.
# The newest proto already uses trade_instance names, but the old Rust files may still contain old RPC/response types.
# Do not use this script as a proto migration; use it only for the DB-facing rename.
Write-Host "Applied simple trade_instance DB rename. Run cargo fmt and cargo check next."
