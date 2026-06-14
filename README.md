# eve-trade

eve-trade is a modular MMORPG trade-system in development, which integrates into a game-server, receives trade requests from that game-server, performs them, and returns a response.

## Goal

incrementally grow into a production ready trade-system that is compatible with the trade system of EVE Online

## Current Status

This project is currently capable of performing a trade request lifecycle starting from api-gateway receiving requests from game server, and translating it to a language that this system can understand by using proto file convention as reference. api-gateway sends the requests to market microservice, which defines the trade mechanics, and makes decisions and sends them to trade settlemenmt. trade-settlement is a separate microservice that is entirely decoupled from trade logic and is responsible for ensuring the reliability of database transactions, transferring item ownership from one capsuleer to another, transferring ISK from one wallet to another, escrowing items and ISK.

This project users Kubernetes to orchestrate containers. It uses Kustomize to organize the manifests into separate categories for observability, chaos engineering, network, and production overlay. This project uses Honeycomb and OpenTelemetry for obserability, Litmus for Chaos engineering to ensure the system can perform despite individual component failure. eve-trade uses Dagger to enable CI/CD pipelines to be written using Python to give the developer more freedom and flexibility to implement complex operations and leverages GitLab to unify the entire continuous integration and deployment of the project. It also uses Rabbitmq as the primary message broker for this project.
