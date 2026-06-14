from __future__ import annotations

import os
from collections.abc import Iterable, Iterator
from pathlib import Path

import grpc


def secure_grpc_enabled() -> bool:
    return os.environ.get("EVE_TRADE_GRPC_TLS", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def grpc_channel(target: str):
    if not secure_grpc_enabled():
        return grpc.insecure_channel(target)

    root_cert_path = os.environ.get("EVE_TRADE_GRPC_ROOT_CERT")
    root_certificates = None
    if root_cert_path:
        root_certificates = Path(root_cert_path).read_bytes()
    return grpc.secure_channel(target, grpc.ssl_channel_credentials(root_certificates))


def settlement_stream_responses(proto, target: str, commands: Iterable, timeout_seconds: float = 10):
    channel = grpc_channel(target)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout_seconds)
        stub = proto.settlement_service_grpc.TradeSettlementServiceStub(channel)
        requests = (
            proto.settlement_service.StreamTradeSettlementCommandsRequest(command=command)
            for command in commands
        )
        responses: Iterator = stub.StreamTradeSettlementCommands(
            requests, timeout=timeout_seconds
        )
        return [response.result for response in responses]
    finally:
        channel.close()


def single_settlement_response(proto, target: str, command, timeout_seconds: float = 10):
    responses = settlement_stream_responses(
        proto, target, [command], timeout_seconds=timeout_seconds
    )
    assert len(responses) == 1
    return responses[0]


def market_stream_responses(proto, target: str, interactions: Iterable, timeout_seconds: float = 10):
    channel = grpc_channel(target)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout_seconds)
        stub = proto.market_service_grpc.MarketTradeServiceStub(channel)
        requests = (
            proto.market_service.StreamProjectTradeInteractionsRequest(
                interaction=interaction
            )
            for interaction in interactions
        )
        responses: Iterator = stub.StreamProjectTradeInteractions(
            requests, timeout=timeout_seconds
        )
        return [response.result for response in responses]
    finally:
        channel.close()


def single_market_response(proto, target: str, interaction, timeout_seconds: float = 10):
    responses = market_stream_responses(
        proto, target, [interaction], timeout_seconds=timeout_seconds
    )
    assert len(responses) == 1
    return responses[0]


def gateway_stream_responses(proto, target: str, activities: Iterable, timeout_seconds: float = 10):
    channel = grpc_channel(target)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout_seconds)
        stub = proto.gateway_service_grpc.GameTradeGatewayServiceStub(channel)
        requests = (
            proto.gateway_service.StreamGameTradeUiActivitiesRequest(activity=activity)
            for activity in activities
        )
        responses: Iterator = stub.StreamGameTradeUiActivities(
            requests, timeout=timeout_seconds
        )
        return [response.result for response in responses]
    finally:
        channel.close()


def single_gateway_response(proto, target: str, activity, timeout_seconds: float = 10):
    responses = gateway_stream_responses(
        proto, target, [activity], timeout_seconds=timeout_seconds
    )
    assert len(responses) == 1
    return responses[0]
