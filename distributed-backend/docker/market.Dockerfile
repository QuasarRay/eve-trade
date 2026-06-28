# This block chooses the Go toolchain image used to compile the market binary.
# It exists because the market service must be built from the same repository checkout as the tests.
FROM golang:1.26-bookworm AS build

WORKDIR /workspace
ARG GO_MODULE_PROXY=https://proxy.golang.org,direct
ENV GOPROXY=${GO_MODULE_PROXY}

COPY distributed-backend/docker/http-healthcheck.go ./distributed-backend/docker/
COPY distributed-backend/src/observability/go.mod distributed-backend/src/observability/go.sum ./distributed-backend/src/observability/
COPY distributed-backend/proto/go.mod distributed-backend/proto/go.sum ./distributed-backend/proto/
COPY distributed-backend/src/messaging/go.mod distributed-backend/src/messaging/go.sum ./distributed-backend/src/messaging/
COPY distributed-backend/src/market/go.mod distributed-backend/src/market/go.sum ./distributed-backend/src/market/
RUN --mount=type=cache,target=/go/pkg/mod,sharing=locked \
    cd distributed-backend/src/market \
    && for attempt in 1 2 3; do \
         go mod download && break; \
         status=$?; \
         [ "$attempt" -eq 3 ] && exit "$status"; \
         sleep $((attempt * 2)); \
       done

COPY distributed-backend/src/observability ./distributed-backend/src/observability
COPY distributed-backend/proto ./distributed-backend/proto
COPY distributed-backend/src/messaging ./distributed-backend/src/messaging
COPY distributed-backend/src/market ./distributed-backend/src/market

RUN --mount=type=cache,target=/go/pkg/mod,sharing=locked \
    --mount=type=cache,target=/root/.cache/go-build \
    cd distributed-backend/src/market \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/market ./cmd/market
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/http-healthcheck ./distributed-backend/docker/http-healthcheck.go

FROM scratch AS runtime

WORKDIR /app

COPY --from=build /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --from=build /out/market /app/market
COPY --from=build /out/http-healthcheck /app/http-healthcheck

EXPOSE 8081
USER 10001:10001

CMD ["/app/market"]
