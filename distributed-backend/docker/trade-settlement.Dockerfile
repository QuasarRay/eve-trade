# This block chooses the Rust toolchain image used to compile the settlement binary.
# It exists because the service is Rust and builds protobuf bindings through the vendored protoc crate.
FROM rust:1-bookworm AS build

# This block sets the repository root as the build working directory.
# It exists so relative paths inside build.rs continue to resolve exactly as they do in the repo.
WORKDIR /workspace

# This block copies the full repository into the build image.
# It exists because the Rust crate reads shared protobuf files outside its own package directory.
COPY . .

# This block builds the  trade-settlement executable in locked mode.
# It exists so the container cannot silently change dependency versions compared with Cargo.lock.
RUN cd distributed-backend/src/trade-settlement \
    && cargo build --locked --release

# This block chooses a small Debian runtime image instead of shipping the full Rust compiler image.
# It exists so the runtime container is closer to a deployable service image.
FROM debian:bookworm-slim AS runtime

# This block creates the runtime working directory.
# It exists so the Summer config file can live beside the binary in a stable location.
WORKDIR /app

# This block copies certificate roots from the toolchain image without invoking OS package managers.
# It exists so rustls-based database dependencies retain the trust store expected by production images.
COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt

# This block copies the compiled settlement binary from the build stage.
# It exists so the runtime image contains only the executable, not the compiler toolchain.
COPY --from=build /workspace/distributed-backend/src/trade-settlement/target/release/trade-settlement /app/trade-settlement

# This block copies the Summer gRPC configuration expected by the service.
# It exists so the container binds to 0.0.0.0:9092 instead of a developer-only default.
COPY --from=build /workspace/distributed-backend/src/trade-settlement/config /app/config

# This block documents the gRPC port exposed by trade-settlement.
# It exists so compose and humans can see the intended network contract.
EXPOSE 9092

# This block runs the  settlement process.
# It exists as the only runtime command for this image.
USER 10001:10001
CMD ["/app/trade-settlement"]
