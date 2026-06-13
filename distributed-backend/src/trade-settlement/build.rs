use std::{env, fs, path::Path, path::PathBuf};

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let manifest_dir = PathBuf::from(env::var("CARGO_MANIFEST_DIR")?);
    let proto_root = manifest_dir.join("..").join("..").join("proto");
    let eve_trade_root = proto_root.join("eve_trade");

    let mut protos = Vec::new();
    collect_proto_files(&eve_trade_root, &mut protos)?;
    protos.sort();

    if protos.is_empty() {
        panic!("No proto files found under {}", eve_trade_root.display());
    }

    for proto in &protos {
        println!("cargo:rerun-if-changed={}", proto.display());
    }

    tonic_prost_build::configure().compile_protos(&protos, &[proto_root])?;

    Ok(())
}

fn collect_proto_files(dir: &Path, protos: &mut Vec<PathBuf>) -> Result<(), std::io::Error> {
    for entry in fs::read_dir(dir)? {
        let entry = entry?;
        let path = entry.path();

        if path.is_dir() {
            collect_proto_files(&path, protos)?;
        } else if path
            .extension()
            .is_some_and(|extension| extension == "proto")
        {
            protos.push(path);
        }
    }

    Ok(())
}
