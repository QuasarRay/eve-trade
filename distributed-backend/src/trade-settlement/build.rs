fn main() -> Result<(), Box<dyn std::error::Error>> {
    let protoc = bundled_protoc_bin_path()?;
    std::env::set_var("PROTOC", protoc);

    let proto = "../../../proto/eve/trade_settlement/v1/trade_settlement.proto";
    let includes = ["../../../proto"];
    let descriptor_path =
        std::path::PathBuf::from(std::env::var("OUT_DIR")?).join("file_descriptor_set.bin");

    println!("cargo:rerun-if-changed={proto}");
    println!("cargo:rerun-if-changed=../../../proto/eve/validation/v1/validation_rules.proto");
    println!("cargo:rerun-if-changed=../../../proto/buf/validate/validate.proto");

    let mut prost_config = tonic_prost_build::Config::new();
    prost_reflect_build::Builder::new()
        .file_descriptor_set_path(&descriptor_path)
        .file_descriptor_set_bytes("crate::proto::FILE_DESCRIPTOR_SET_BYTES")
        .configure(&mut prost_config, &[proto], &includes)?;

    tonic_prost_build::configure()
        .build_client(true)
        .build_server(true)
        .file_descriptor_set_path(&descriptor_path)
        .skip_protoc_run()
        .compile_with_config(prost_config, &[proto], &includes)?;

    Ok(())
}

#[cfg(all(target_os = "linux", target_arch = "x86_64"))]
fn bundled_protoc_bin_path() -> Result<std::path::PathBuf, Box<dyn std::error::Error>> {
    Ok(protoc_bin_vendored_linux_x86_64::protoc_bin_path())
}

#[cfg(windows)]
fn bundled_protoc_bin_path() -> Result<std::path::PathBuf, Box<dyn std::error::Error>> {
    Ok(protoc_bin_vendored_win32::protoc_bin_path())
}

#[cfg(not(any(all(target_os = "linux", target_arch = "x86_64"), windows)))]
fn bundled_protoc_bin_path() -> Result<std::path::PathBuf, Box<dyn std::error::Error>> {
    match std::env::var_os("PROTOC") {
        Some(path) => Ok(path.into()),
        None => Err("set PROTOC for this build platform".into()),
    }
}
