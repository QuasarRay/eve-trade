FROM rust:1-bookworm@sha256:19817ead3289c8c631c73df281e18b59b172f6a31f4f563290f69cddd06c30e9 AS build

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

FROM debian:bookworm-slim@sha256:96e378d7e6531ac9a15ad505478fcc2e69f371b10f5cdf87857c4b8188404716 AS runtime

COPY --from=build /opt/quilkin/bin/quilkin /usr/local/bin/quilkin

EXPOSE 26001/udp

USER 10001:10001
ENTRYPOINT ["/usr/local/bin/quilkin"]
