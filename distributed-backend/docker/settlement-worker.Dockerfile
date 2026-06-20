FROM golang:1.26-bookworm AS build

WORKDIR /workspace

COPY distributed-backend/src/observability/go.mod distributed-backend/src/observability/go.sum ./distributed-backend/src/observability/
COPY distributed-backend/proto/go.mod distributed-backend/proto/go.sum ./distributed-backend/proto/
COPY distributed-backend/src/messaging/go.mod distributed-backend/src/messaging/go.sum ./distributed-backend/src/messaging/
COPY distributed-backend/src/settlement-worker/go.mod distributed-backend/src/settlement-worker/go.sum ./distributed-backend/src/settlement-worker/
RUN cd distributed-backend/src/settlement-worker && go mod download

COPY distributed-backend/src/observability ./distributed-backend/src/observability
COPY distributed-backend/proto ./distributed-backend/proto
COPY distributed-backend/src/messaging ./distributed-backend/src/messaging
COPY distributed-backend/src/settlement-worker ./distributed-backend/src/settlement-worker

RUN cd distributed-backend/src/settlement-worker \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/settlement-worker ./cmd/settlement-worker

FROM debian:bookworm-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl passwd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser

COPY --from=build /out/settlement-worker /app/settlement-worker
RUN chown appuser:appuser /app/settlement-worker

EXPOSE 8082
USER appuser

CMD ["/app/settlement-worker"]
