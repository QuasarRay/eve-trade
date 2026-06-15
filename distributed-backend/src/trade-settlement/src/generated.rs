pub mod eve_trade {
    pub mod common {
        pub mod v1 {
            #![allow(dead_code, clippy::enum_variant_names, clippy::large_enum_variant)]
            tonic::include_proto!("eve_trade.common.v1");
        }
    }

    pub mod domain {
        pub mod trade {
            pub mod v1 {
                #![allow(dead_code, clippy::enum_variant_names, clippy::large_enum_variant)]
                tonic::include_proto!("eve_trade.domain.trade.v1");
            }
        }
    }

    pub mod gateway {
        pub mod v1 {
            #![allow(dead_code, clippy::enum_variant_names, clippy::large_enum_variant)]
            tonic::include_proto!("eve_trade.gateway.v1");
        }
    }

    pub mod market {
        pub mod v1 {
            #![allow(dead_code, clippy::enum_variant_names, clippy::large_enum_variant)]
            tonic::include_proto!("eve_trade.market.v1");
        }
    }

    pub mod operation {
        pub mod v1 {
            #![allow(dead_code, clippy::enum_variant_names, clippy::large_enum_variant)]
            tonic::include_proto!("eve_trade.operation.v1");
        }
    }

    pub mod settlement {
        pub mod v1 {
            #![allow(dead_code, clippy::enum_variant_names, clippy::large_enum_variant)]
            tonic::include_proto!("eve_trade.settlement.v1");
        }
    }
}
