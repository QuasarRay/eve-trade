from __future__ import annotations

from collections.abc import Iterator

import grpc


def single_settlement_response(proto, target: str, command, timeout_seconds: float = 10):
    channel = grpc.insecure_channel(target)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout_seconds)
        stub = proto.settlement_service_grpc.TradeSettlementServiceStub(channel)
        request = proto.settlement_service.StreamTradeSettlementCommandsRequest(
            command=command
        )
        responses: Iterator = stub.StreamTradeSettlementCommands(
            iter([request]), timeout=timeout_seconds
        )
        return next(responses).result
    finally:
        channel.close()


def single_market_response(proto, target: str, interaction, timeout_seconds: float = 10):
    channel = grpc.insecure_channel(target)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout_seconds)
        stub = proto.market_service_grpc.MarketTradeServiceStub(channel)
        request = proto.market_service.StreamProjectTradeInteractionsRequest(
            interaction=interaction
        )
        responses: Iterator = stub.StreamProjectTradeInteractions(
            iter([request]), timeout=timeout_seconds
        )
        return next(responses).result
    finally:
        channel.close()


def single_gateway_response(proto, target: str, activity, timeout_seconds: float = 10):
    channel = grpc.insecure_channel(target)
    try:
        grpc.channel_ready_future(channel).result(timeout=timeout_seconds)
        stub = proto.gateway_service_grpc.GameTradeGatewayServiceStub(channel)
        request = proto.gateway_service.StreamGameTradeUiActivitiesRequest(
            activity=activity
        )
        responses: Iterator = stub.StreamGameTradeUiActivities(
            iter([request]), timeout=timeout_seconds
        )
        return next(responses).result
    finally:
        channel.close()
