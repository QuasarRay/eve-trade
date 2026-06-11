// This build script exists because the Rust gRPC server must use generated
// protobuf and tonic code that exactly matches the settlement.proto contract.
// Keeping code generation in build.rs prevents hand-written transport structs
// from drifting away from the proto API used by market.
use std::{env, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR")?);

    let proto_root = manifest_dir.join("..").join("..").join("proto");

    let protos = [
        proto_root.join("trade/v1/common.proto"),
        proto_root.join("trade/v1/operation.proto"),
        proto_root.join("trade/v1/ownership.proto"),
        proto_root.join("trade/v1/market_types.proto"),
        proto_root.join("settlement/v1/trade_settlement.proto"),
    ];

    for proto in &protos {
        println!("cargo:rerun-if-changed={}", proto.display());

        if !proto.exists() {
            panic!("Missing proto file: {}", proto.display());
        }
    }

    tonic_prost_build::configure().compile_protos(&protos, &[proto_root])?;

    Ok(())
}
