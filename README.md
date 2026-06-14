# eve-trade

`eve-trade` is a modular MMORPG trade system in development. It is designed to integrate with a game server, receive trade requests from that game server, process them through distributed backend services, perform durable settlement operations, and return a result.

## Goal

The goal of `eve-trade` is to incrementally grow into a production-ready MMORPG trade system inspired by EVE Online-style market and trade mechanics.

The project focuses on the backend and platform engineering problems behind player trading: service boundaries, settlement reliability, database-backed ownership transfer, message-driven workflows, observability, chaos testing, CI/CD automation, Kubernetes orchestration, and cloud deployment infrastructure.

## Current Status

`eve-trade` is currently capable of performing a trade request lifecycle starting from the API Gateway receiving a request from a game server.

The API Gateway translates the request into the internal protocol convention defined by the project’s protobuf contracts, then forwards it to the Market service. The Market service owns trade-mechanic decisions and sends settlement requests to `trade-settlement`.

`trade-settlement` is a separate microservice decoupled from market trade logic. Its responsibility is to protect correctness-critical persistence: reliable database transactions, item ownership transfer, ISK wallet transfer, escrow handling, and settlement state management.

## Platform Capabilities

The project uses Kubernetes to orchestrate containers and Kustomize to organize manifests across application deployment, networking, observability, chaos engineering, and production overlays.

The platform side currently includes:

* Kubernetes manifests for service orchestration
* Kustomize-based manifest organization
* RabbitMQ as the primary message broker
* Honeycomb and OpenTelemetry for observability and tracing
* Litmus for chaos engineering experiments
* Dagger-based CI/CD pipeline logic written in Python
* GitLab CI/CD integration for pipeline execution
* Terraform manifests for provisioning AWS infrastructure
* EKS deployment foundation for running the system on AWS

## AWS / EKS Infrastructure

The project includes Terraform manifests for provisioning AWS infrastructure required to deploy `eve-trade` on AWS, including an EKS-based Kubernetes environment. This gives the project a cloud deployment path instead of limiting it to local Docker or static Kubernetes manifests.
