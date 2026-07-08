pub const FILE_DESCRIPTOR_SET_BYTES: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/file_descriptor_set.bin"));

pub mod trade_settlement {
    tonic::include_proto!("eve.trade_settlement.v1");
}
