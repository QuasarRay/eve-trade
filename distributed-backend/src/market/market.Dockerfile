# This block chooses the Go toolchain image used to compile the market binary.
# It exists because the market service must be built from the same repository checkout as the tests.
FROM golang:1.26-bookworm AS build

# This block sets the repository root as the build working directory.
# It exists so module-relative imports and generated protobuf paths resolve correctly.
WORKDIR /workspace

# This block copies the full repository into the build image.
# It exists because market imports generated protobuf packages from distributed-backend/proto/gen.
COPY . .

# This block builds the  market executable.
# It exists so compose runs the production market wiring with the  settlement client.
RUN go build -o /out/market ./distributed-backend/src/market/cmd/market

# This block chooses a small Debian runtime image instead of shipping the full Go compiler image.
# It exists so the runtime image is closer to a deployable service image.
FROM debian:bookworm-slim AS runtime

# This block installs certificate roots for outbound HTTP/gRPC-compatible clients if TLS is enabled later.
# It exists as a small production-shaped default even though the local compose network uses plain HTTP.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# This block creates the runtime working directory.
# It exists so the binary has a stable launch location.
WORKDIR /app

# This block copies the compiled market binary from the build stage.
# It exists so the runtime image contains only the executable, not the Go toolchain.
COPY --from=build /out/market /app/market

# This block documents the market service port used by the e2e tests.
# It exists so compose and humans can see the intended network contract.
EXPOSE 8081

# This block runs the  market process.
# It exists as the only runtime command for this image.
CMD ["/app/market"]
