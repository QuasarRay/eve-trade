// This build script exists because the Rust gRPC server must use generated
// protobuf and tonic code that exactly matches the settlement.proto contract.
// Keeping code generation in build.rs prevents hand-written transport structs
// from drifting away from the proto API used by market.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_prost_build::configure().compile_protos(
        &[
            "../../proto/trade/v1/common.proto",
            "../../proto/trade/v1/market_types.proto",
            "../../proto/trade/v1/operation.proto",
            "../../proto/trade/v1/ownership.proto",
            "../../proto/trade-settlement/v1/trade-settlement.proto",
        ],
        &["../../proto"],
    )?;

    Ok(())
}