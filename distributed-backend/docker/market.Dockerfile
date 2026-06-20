# This block chooses the Go toolchain image used to compile the market binary.
# It exists because the market service must be built from the same repository checkout as the tests.
FROM golang:1.26-bookworm AS build

WORKDIR /workspace

COPY distributed-backend/src/observability/go.mod distributed-backend/src/observability/go.sum ./distributed-backend/src/observability/
COPY distributed-backend/proto/go.mod distributed-backend/proto/go.sum ./distributed-backend/proto/
COPY distributed-backend/src/market/go.mod distributed-backend/src/market/go.sum ./distributed-backend/src/market/
RUN cd distributed-backend/src/market && go mod download

COPY distributed-backend/src/observability ./distributed-backend/src/observability
COPY distributed-backend/proto ./distributed-backend/proto
COPY distributed-backend/src/market ./distributed-backend/src/market

RUN cd distributed-backend/src/market \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/market ./cmd/market

FROM debian:bookworm-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl passwd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser

COPY --from=build /out/market /app/market
RUN chown appuser:appuser /app/market

EXPOSE 8081
USER appuser

CMD ["/app/market"]
