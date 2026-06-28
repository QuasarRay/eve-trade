FROM rust:1-bookworm AS build

ARG QUILKIN_VERSION=0.10.0

ENV RUSTFLAGS="-C target-feature=+aes,+sse2"

RUN --mount=type=cache,target=/usr/local/cargo/registry,sharing=locked \
    --mount=type=cache,target=/usr/local/cargo/git,sharing=locked \
    for attempt in 1 2 3; do \
      cargo install quilkin --version "${QUILKIN_VERSION}" --locked --root /opt/quilkin && break; \
      status=$?; \
      [ "$attempt" -eq 3 ] && exit "$status"; \
      sleep $((attempt * 2)); \
    done

FROM debian:bookworm-slim AS runtime

COPY --from=build /opt/quilkin/bin/quilkin /usr/local/bin/quilkin

EXPOSE 26001/udp

USER 10001:10001
ENTRYPOINT ["/usr/local/bin/quilkin"]
