FROM golang:1.26-bookworm@sha256:5f68ec6805843bd3981a951ffada82a26a0bd2631045c8f7dba483fa868f5ec5 AS build

WORKDIR /workspace

COPY distributed-backend/docker/http-healthcheck.go ./distributed-backend/docker/
COPY go.mod go.work go.work.sum ./
COPY vendor ./vendor
COPY distributed-backend/src/observability/go.mod distributed-backend/src/observability/go.sum ./distributed-backend/src/observability/
COPY distributed-backend/proto/go.mod distributed-backend/proto/go.sum ./distributed-backend/proto/
COPY distributed-backend/src/api-gateway/go.mod distributed-backend/src/api-gateway/go.sum ./distributed-backend/src/api-gateway/
COPY distributed-backend/src/market/go.mod distributed-backend/src/market/go.sum ./distributed-backend/src/market/
COPY distributed-backend/src/messaging/go.mod distributed-backend/src/messaging/go.sum ./distributed-backend/src/messaging/
COPY distributed-backend/src/settlement-worker/go.mod distributed-backend/src/settlement-worker/go.sum ./distributed-backend/src/settlement-worker/

COPY distributed-backend/src/observability ./distributed-backend/src/observability
COPY distributed-backend/proto ./distributed-backend/proto
COPY distributed-backend/src/api-gateway ./distributed-backend/src/api-gateway

RUN --mount=type=cache,target=/go/pkg/mod,sharing=locked \
    --mount=type=cache,target=/root/.cache/go-build \
    cd distributed-backend/src/api-gateway \
    && CGO_ENABLED=0 go build -mod=vendor -trimpath -ldflags="-s -w" -o /out/api-gateway ./cmd/api-gateway
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/http-healthcheck ./distributed-backend/docker/http-healthcheck.go

FROM scratch AS runtime

WORKDIR /app

COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/api-gateway /app/api-gateway
COPY --from=build /out/http-healthcheck /app/http-healthcheck

EXPOSE 8080
EXPOSE 26000/udp
USER 10001:10001

CMD ["/app/api-gateway"]
