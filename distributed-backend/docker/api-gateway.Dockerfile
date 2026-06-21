FROM golang:1.26-bookworm AS build

WORKDIR /workspace

COPY distributed-backend/src/observability/go.mod distributed-backend/src/observability/go.sum ./distributed-backend/src/observability/
COPY distributed-backend/proto/go.mod distributed-backend/proto/go.sum ./distributed-backend/proto/
COPY distributed-backend/src/api-gateway/go.mod distributed-backend/src/api-gateway/go.sum ./distributed-backend/src/api-gateway/
RUN cd distributed-backend/src/api-gateway && go mod download

COPY distributed-backend/src/observability ./distributed-backend/src/observability
COPY distributed-backend/proto ./distributed-backend/proto
COPY distributed-backend/src/api-gateway ./distributed-backend/src/api-gateway

RUN cd distributed-backend/src/api-gateway \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/api-gateway ./cmd/api-gateway

FROM debian:bookworm-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl passwd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser

COPY --from=build /out/api-gateway /app/api-gateway
RUN chown appuser:appuser /app/api-gateway

EXPOSE 8080
USER appuser

CMD ["/app/api-gateway"]
