# eve-trade

`eve-trade` is a modular MMORPG trade system in development. It is designed to integrate with a game server, receive trade requests from that game server, process them through distributed backend services, perform durable settlement operations, and return a result.

## Run Locally

Prerequisite: install Docker Desktop and make sure it is allowed to run Linux containers.

One command from the repository root:

```bash
docker compose up --build
```

No CLI on Windows:

1. Double-click `run-eve-trade.cmd`.
2. Wait for Docker images to build and services to start.
3. Stop everything by closing the launcher window with `Ctrl+C`, or double-click `stop-eve-trade.cmd`.

VS Code button path:

1. Open the repository in VS Code.
2. Run `Terminal: Run Build Task`.
3. Choose `Run eve-trade` if VS Code asks.

The local stack starts:

* API Gateway: `http://localhost:8080`
* RabbitMQ AMQP: `localhost:5672`
* RabbitMQ management UI: `http://localhost:15672` (`eve_trade` / `eve_trade`)
* PostgreSQL: `localhost:5432`

The startup migration creates the local database schema when it is missing,
applies compatible follow-up migrations, and seeds a small local world. The
main sample actors are seller capsuleer `1001`, buyer capsuleer `2002`, seller
Tritanium stack `11111111-1111-4111-8111-111111111111`, buyer wallet
`00000000-0000-4000-8000-000000002002`, item type `34`, and station
`60003760`. To reset everything, run `docker compose down -v` before starting
the stack again. This project currently exposes backend services, not a browser
UI.

## Goal

The goal of `eve-trade` is to incrementally grow into a production-ready MMORPG trade system inspired by EVE Online-style market and trade mechanics.

The project focuses on the backend and platform engineering problems behind player trading: service boundaries, settlement reliability, database-backed ownership transfer, message-driven workflows, observability, chaos testing, CI/CD automation, Kubernetes orchestration, and cloud deployment infrastructure.

## Current Status

`eve-trade` is currently capable of performing a trade request lifecycle starting from the API Gateway receiving a request from a game server.

The API Gateway translates the request into the internal protocol convention defined by the project's protobuf contracts, then forwards it to the Market service. The Market service owns trade-mechanic decisions and publishes settlement commands through RabbitMQ. `settlement-worker` consumes those commands and calls `trade-settlement`.

`trade-settlement` is a separate microservice decoupled from market trade logic. Its responsibility is to protect correctness-critical persistence: reliable database transactions, item ownership transfer, ISK wallet transfer, escrow handling, and settlement state management.

## Platform Capabilities

The project uses Kubernetes to orchestrate containers and Kustomize to organize manifests across application deployment, networking, observability, chaos engineering, and production overlays.

The platform side currently includes:

* Kubernetes manifests for service orchestration
* Kustomize-based manifest organization
* API Gateway -> Market -> RabbitMQ -> settlement-worker -> trade-settlement service flow
* OpenTelemetry-based observability with Honeycomb, Sentry, and Prometheus
* Litmus for chaos engineering experiments
* Dagger-based CI/CD pipeline logic written in Python
* GitLab CI/CD integration for pipeline execution
* Terraform manifests for provisioning AWS infrastructure
* EKS deployment foundation for running the system on AWS

## AWS / EKS Infrastructure

The project includes Terraform manifests for provisioning AWS infrastructure required to deploy `eve-trade` on AWS, including an EKS-based Kubernetes environment. This gives the project a cloud deployment path instead of limiting it to local Docker or static Kubernetes manifests.
