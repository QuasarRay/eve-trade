# This block chooses the Rust toolchain image used to compile the settlement binary.
# It exists because the service is Rust and builds protobuf bindings through the vendored protoc crate.
FROM rust:1-bookworm@sha256:19817ead3289c8c631c73df281e18b59b172f6a31f4f563290f69cddd06c30e9 AS build

# This block sets the repository root as the build working directory.
# It exists so relative paths inside build.rs continue to resolve exactly as they do in the repo.
WORKDIR /workspace

# This block copies the full repository into the build image.
# It exists because the Rust crate reads shared protobuf files outside its own package directory.
COPY . .

# This block fetches the locked graph, exposes the pinned vendored protoc to
# every dependency build script, and builds the trade-settlement executable.
# The crate's own build.rs runs too late to configure protoc for transitive
# protobuf build scripts, so the Docker build must provide it globally.
RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/usr/local/cargo/git,sharing=locked \
    cd distributed-backend/src/trade-settlement \
    && for attempt in 1 2 3; do \
         cargo fetch --locked \
         && PROTOC="$(find /usr/local/cargo/registry/src -path '*/protoc-bin-vendored-linux-x86_64-3.2.0/bin/protoc' -print -quit)" \
         && test -x "$PROTOC" \
         && PROTOC="$PROTOC" cargo build --locked --release \
         && break; \
         status=$?; \
         [ "$attempt" -eq 3 ] && exit "$status"; \
         sleep $((attempt * 2)); \
       done

# This block chooses a small Debian runtime image instead of shipping the full Rust compiler image.
# It exists so the runtime container is closer to a deployable service image.
FROM debian:bookworm-slim@sha256:96e378d7e6531ac9a15ad505478fcc2e69f371b10f5cdf87857c4b8188404716 AS runtime

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
