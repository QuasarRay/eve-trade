package main

import (
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/QuasarRay/eve-trade/messaging/rabbitmqsettlement"
)

type Config struct {
	HealthHTTPAddr           string
	TradeSettlementURL       string
	SettlementRequestTimeout time.Duration
	RabbitMQ                 rabbitmqsettlement.Config
}

func LoadConfig() Config {
	settlementTimeout := durationEnvOr("SETTLEMENT_WORKER_REQUEST_TIMEOUT", 30*time.Second)
	return Config{
		HealthHTTPAddr:           envOr("SETTLEMENT_WORKER_HEALTH_HTTP_ADDR", ":8082"),
		TradeSettlementURL:       trimRightSlash(envOr("TRADE_SETTLEMENT_URL", "http://localhost:9090")),
		SettlementRequestTimeout: settlementTimeout,
		RabbitMQ: rabbitmqsettlement.Config{
			URL:                  envOr("RABBITMQ_URL", rabbitmqsettlement.DefaultURL),
			Exchange:             envOr("RABBITMQ_SETTLEMENT_EXCHANGE", rabbitmqsettlement.DefaultExchange),
			CommandQueue:         envOr("RABBITMQ_SETTLEMENT_COMMAND_QUEUE", rabbitmqsettlement.DefaultCommandQueue),
			RoutingKey:           envOr("RABBITMQ_SETTLEMENT_ROUTING_KEY", rabbitmqsettlement.DefaultRoutingKey),
			DeadLetterExchange:   envOr("RABBITMQ_SETTLEMENT_DLX", rabbitmqsettlement.DefaultDeadLetterExchange),
			DeadLetterQueue:      envOr("RABBITMQ_SETTLEMENT_DEAD_QUEUE", rabbitmqsettlement.DefaultDeadLetterQueue),
			DeadLetterRoutingKey: envOr("RABBITMQ_SETTLEMENT_DEAD_ROUTING_KEY", rabbitmqsettlement.DefaultDeadLetterRoutingKey),
			RequestTimeout:       settlementTimeout,
			PublishTimeout:       durationEnvOr("RABBITMQ_PUBLISH_TIMEOUT", rabbitmqsettlement.DefaultPublishTimeout),
			PrefetchCount:        intEnvOr("RABBITMQ_SETTLEMENT_PREFETCH", rabbitmqsettlement.DefaultPrefetchCount),
		}.WithDefaults(),
	}
}

func envOr(name string, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func trimRightSlash(value string) string {
	return strings.TrimRight(value, "/")
}

func durationEnvOr(name string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	duration, err := time.ParseDuration(value)
	if err != nil {
		return fallback
	}
	return duration
}

func intEnvOr(name string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}
