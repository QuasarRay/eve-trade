fn main() -> Result<(), Box<dyn std::error::Error>> {
    let protoc = bundled_protoc_bin_path()?;
    std::env::set_var("PROTOC", protoc);

    let proto = "../../proto/eve/trade_settlement/v1/trade_settlement.proto";
    println!("cargo:rerun-if-changed={proto}");

    tonic_prost_build::configure()
        .build_client(true)
        .build_server(true)
        .compile_protos(&[proto], &["../../proto"])?;

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
