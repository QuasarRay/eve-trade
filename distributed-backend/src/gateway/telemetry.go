package gateway

import (
	"context"
	"log/slog"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/metric"
)

var (
	udpMeter           = otel.Meter("github.com/QuasarRay/eve-trade/gateway/udp")
	udpTracer          = otel.Tracer("github.com/QuasarRay/eve-trade/gateway/udp")
	udpPacketCounter   metric.Int64Counter
	udpPacketBytes     metric.Int64Histogram
	udpDownstreamCalls metric.Float64Histogram
)

func init() {
	var err error
	udpPacketCounter, err = udpMeter.Int64Counter("eve_trade_api_gateway_udp_packets_total")
	if err != nil {
		slog.Warn("create udp packet counter failed", "error", err)
	}
	udpPacketBytes, err = udpMeter.Int64Histogram("eve_trade_api_gateway_udp_packet_bytes")
	if err != nil {
		slog.Warn("create udp packet size histogram failed", "error", err)
	}
	udpDownstreamCalls, err = udpMeter.Float64Histogram("eve_trade_api_gateway_udp_downstream_seconds")
	if err != nil {
		slog.Warn("create udp downstream histogram failed", "error", err)
	}
}

func recordUDPPacket(ctx context.Context, outcome string, bytes int) {
	if udpPacketCounter != nil {
		udpPacketCounter.Add(ctx, 1, metric.WithAttributes(attribute.String("outcome", outcome)))
	}
	if udpPacketBytes != nil && bytes >= 0 {
		udpPacketBytes.Record(ctx, int64(bytes), metric.WithAttributes(attribute.String("outcome", outcome)))
	}
}

func recordUDPDownstream(ctx context.Context, elapsed time.Duration, err error) {
	if udpDownstreamCalls == nil {
		return
	}
	outcome := "success"
	if err != nil {
		outcome = stableDownstreamCode(err)
	}
	udpDownstreamCalls.Record(ctx, elapsed.Seconds(), metric.WithAttributes(attribute.String("outcome", outcome)))
}
