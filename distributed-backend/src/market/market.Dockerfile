# This block chooses the Go toolchain image used to compile the market binary.
# It exists because the market service must be built from the same repository checkout as the tests.
FROM golang:1.26-bookworm AS build

WORKDIR /workspace

COPY go.mod go.sum ./
RUN go mod download

COPY . .

RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/market ./distributed-backend/src/market/cmd/market

FROM debian:bookworm-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates passwd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin appuser

COPY --from=build /out/market /app/market
RUN chown appuser:appuser /app/market

EXPOSE 8081
USER appuser

CMD ["/app/market"]
