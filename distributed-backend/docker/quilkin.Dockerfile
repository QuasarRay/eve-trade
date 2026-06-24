FROM rust:1-bookworm AS build

ARG QUILKIN_VERSION=0.10.0

ENV RUSTFLAGS="-C target-feature=+aes,+sse2"

RUN cargo install quilkin --version "${QUILKIN_VERSION}" --locked --root /opt/quilkin

FROM debian:bookworm-slim AS runtime

COPY --from=build /opt/quilkin/bin/quilkin /usr/local/bin/quilkin

EXPOSE 26001/udp

USER 10001:10001
ENTRYPOINT ["/usr/local/bin/quilkin"]
