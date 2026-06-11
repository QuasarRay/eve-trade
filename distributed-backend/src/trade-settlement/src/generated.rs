// Generated protobuf include boundary.
//
// What this file does:
// - Exposes generated Rust modules with the same shape as protobuf packages.
//
// How it works:
// - `tonic::include_proto!("trade.v1")` includes generated code for package trade.v1.
// - It must live under `generated::trade::v1` because generated settlement code
//   references shared trade types through `super::super::trade::v1`.
//
// Why it exists:
// - Without this nested shape, generated settlement code fails with
//   `could not find trade in the crate root`.

// DB-BLOCK src_replacements_generated_001
// What: exposes the `trade` submodule.
// How: makes `trade.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod trade {
    // DB-BLOCK src_replacements_generated_002
    // What: exposes the `v1` submodule.
    // How: makes `v1.rs` part of the Rust module tree.
    // Why: the DB project is split by responsibility instead of becoming one unsafe file.
    pub mod v1 {
        #![allow(dead_code)]
        tonic::include_proto!("trade.v1");
    }
}

// DB-BLOCK src_replacements_generated_003
// What: exposes the `settlement` submodule.
// How: makes `settlement.rs` part of the Rust module tree.
// Why: the DB project is split by responsibility instead of becoming one unsafe file.
pub mod settlement {
    // DB-BLOCK src_replacements_generated_004
    // What: exposes the `v1` submodule.
    // How: makes `v1.rs` part of the Rust module tree.
    // Why: the DB project is split by responsibility instead of becoming one unsafe file.
    pub mod v1 {
        #![allow(dead_code)]
        tonic::include_proto!("settlement.v1");
    }
}
