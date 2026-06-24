FROM golang:1.26-bookworm AS build

WORKDIR /workspace

COPY distributed-backend/docker/http-healthcheck.go ./distributed-backend/docker/
COPY distributed-backend/src/observability/go.mod distributed-backend/src/observability/go.sum ./distributed-backend/src/observability/
COPY distributed-backend/proto/go.mod distributed-backend/proto/go.sum ./distributed-backend/proto/
COPY distributed-backend/src/api-gateway/go.mod distributed-backend/src/api-gateway/go.sum ./distributed-backend/src/api-gateway/
RUN cd distributed-backend/src/api-gateway && go mod download

COPY distributed-backend/src/observability ./distributed-backend/src/observability
COPY distributed-backend/proto ./distributed-backend/proto
COPY distributed-backend/src/api-gateway ./distributed-backend/src/api-gateway

RUN cd distributed-backend/src/api-gateway \
    && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/api-gateway ./cmd/api-gateway
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
