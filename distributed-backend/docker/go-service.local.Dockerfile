FROM scratch AS runtime

ARG SERVICE_BINARY
ARG SERVICE_PORT

WORKDIR /app

COPY --from=golang:1.26-bookworm /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
COPY --chmod=0755 distributed-backend/docker/local-bin/${SERVICE_BINARY} /app/service
COPY --chmod=0755 distributed-backend/docker/local-bin/http-healthcheck /app/http-healthcheck

EXPOSE ${SERVICE_PORT}

USER 10001:10001
CMD ["/app/service"]
