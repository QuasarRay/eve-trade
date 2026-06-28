FROM golang:1.26-bookworm AS build

WORKDIR /workspace
ARG GO_MODULE_PROXY=https://proxy.golang.org,direct
ENV GOPROXY=${GO_MODULE_PROXY}

COPY distributed-backend/docker/http-healthcheck.go ./distributed-backend/docker/
COPY distributed-backend/src/observability/go.mod distributed-backend/src/observability/go.sum ./distributed-backend/src/observability/
COPY distributed-backend/proto/go.mod distributed-backend/proto/go.sum ./distributed-backend/proto/
COPY distributed-backend/src/messaging/go.mod distributed-backend/src/messaging/go.sum ./distributed-backend/src/messaging/
COPY distributed-backend/src/settlement-worker/go.mod distributed-backend/src/settlement-worker/go.sum ./distributed-backend/src/settlement-worker/
RUN --mount=type=cache,target=/go/pkg/mod,sharing=locked \
    cd distributed-backend/src/settlement-worker \
    && for attempt in 1 2 3; do \
         go mod download && break; \
         status=$?; \
         [ "$attempt" -eq 3 ] && exit "$status"; \
         sleep $((attempt * 2)); \
       done

COPY distributed-backend/src/observability ./distributed-backend/src/observability
COPY distributed-backend/proto ./distributed-backend/proto
COPY distributed-backend/src/messaging ./distributed-backend/src/messaging
COPY distributed-backend/src/settlement-worker ./distributed-backend/src/settlement-worker

RUN --mount=type=cache,target=/go/pkg/mod,sharing=locked \
    --mount=type=cache,target=/root/.cache/go-build \
    cd distributed-backend/src/settlement-worker \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/settlement-worker ./cmd/settlement-worker
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/http-healthcheck ./distributed-backend/docker/http-healthcheck.go

FROM scratch AS runtime

WORKDIR /app

COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/settlement-worker /app/settlement-worker
COPY --from=build /out/http-healthcheck /app/http-healthcheck

EXPOSE 8082
USER 10001:10001

CMD ["/app/settlement-worker"]
