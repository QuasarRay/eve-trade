// This module is the only place where generated protobuf code is included.
// Keeping it isolated makes the rest of the service read as normal Rust while
// still guaranteeing that the gRPC boundary uses the exact proto contract.
pub mod settlement {
    tonic::include_proto!("settlement.v1");
}
