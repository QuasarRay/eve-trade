package main

import (
	"testing"
	"time"
)

func TestLoadConfigUsesDefaultsAndNormalizesURL(t *testing.T) {
	for _, name := range []string{
		"SETTLEMENT_WORKER_HEALTH_HTTP_ADDR", "TRADE_SETTLEMENT_URL", "SETTLEMENT_WORKER_REQUEST_TIMEOUT",
		"RABBITMQ_URL", "RABBITMQ_SETTLEMENT_EXCHANGE", "RABBITMQ_SETTLEMENT_COMMAND_QUEUE",
		"RABBITMQ_SETTLEMENT_ROUTING_KEY", "RABBITMQ_SETTLEMENT_DLX", "RABBITMQ_SETTLEMENT_DEAD_QUEUE",
		"RABBITMQ_SETTLEMENT_DEAD_ROUTING_KEY", "RABBITMQ_PUBLISH_TIMEOUT", "RABBITMQ_SETTLEMENT_PREFETCH",
	} {
		t.Setenv(name, "")
	}
	config := LoadConfig()
	if config.HealthHTTPAddr != ":8082" || config.TradeSettlementURL != "http://localhost:9090" {
		t.Fatalf("defaults = %+v", config)
	}
	if config.SettlementRequestTimeout != 10*time.Second || config.RabbitMQ.PrefetchCount != 8 {
		t.Fatalf("runtime defaults = %+v", config)
	}
}

func TestLoadConfigUsesValidValuesAndRejectsInvalidNumericValues(t *testing.T) {
	t.Setenv("TRADE_SETTLEMENT_URL", "http://settlement:9092///")
	t.Setenv("SETTLEMENT_WORKER_REQUEST_TIMEOUT", "3s")
	t.Setenv("RABBITMQ_PUBLISH_TIMEOUT", "invalid")
	t.Setenv("RABBITMQ_SETTLEMENT_PREFETCH", "0")
	config := LoadConfig()
	if config.TradeSettlementURL != "http://settlement:9092" || config.SettlementRequestTimeout != 3*time.Second {
		t.Fatalf("explicit config = %+v", config)
	}
	if config.RabbitMQ.PublishTimeout != 5*time.Second || config.RabbitMQ.PrefetchCount != 8 {
		t.Fatalf("invalid values did not fall back: %+v", config.RabbitMQ)
	}
}
