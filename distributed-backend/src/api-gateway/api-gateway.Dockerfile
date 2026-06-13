FROM golang:1.26-bookworm AS build

WORKDIR /workspace

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/api-gateway ./distributed-backend/src/api-gateway/cmd/api-gateway

FROM debian:bookworm-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates passwd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser

COPY --from=build /out/api-gateway /app/api-gateway
RUN chown appuser:appuser /app/api-gateway

EXPOSE 8080
USER appuser

CMD ["/app/api-gateway"]
