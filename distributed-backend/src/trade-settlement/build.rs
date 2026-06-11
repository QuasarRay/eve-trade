// This build script exists because the Rust gRPC server must use generated
// protobuf and tonic code that exactly matches the settlement.proto contract.
// Keeping code generation in build.rs prevents hand-written transport structs
// from drifting away from the proto API used by market.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    tonic_build::configure().compile_protos(
        &["../proto/settlement/v1/settlement.proto"],
        &["../proto"],
    )?;
    Ok(())
}
