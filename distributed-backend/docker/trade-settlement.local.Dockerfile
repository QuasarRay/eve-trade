FROM debian:bookworm-slim@sha256:96e378d7e6531ac9a15ad505478fcc2e69f371b10f5cdf87857c4b8188404716 AS runtime

WORKDIR /app

COPY --from=rust:1-bookworm /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --chmod=0755 distributed-backend/docker/local-bin/trade-settlement /app/trade-settlement
COPY distributed-backend/src/trade-settlement/config /app/config

EXPOSE 9092

USER 10001:10001
CMD ["/app/trade-settlement"]
