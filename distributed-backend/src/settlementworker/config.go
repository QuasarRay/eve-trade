package settlementworker

import (
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	TradeSettlementTarget         string
	TradeSettlementMaxConnections int
	RequestTimeout                time.Duration
	OutboxDispatchInterval        time.Duration
}

const settlementWorkerDefaultRequestTimeout = 10 * time.Second

func LoadConfig() Config {
	return Config{
		TradeSettlementTarget:         envOr("TRADE_SETTLEMENT_GRPC_TARGET", "127.0.0.1:9092"),
		TradeSettlementMaxConnections: intEnvOr("TRADE_SETTLEMENT_DATABASE_MAX_CONNECTIONS", 10),
		RequestTimeout:                durationEnvOr("SETTLEMENT_WORKER_REQUEST_TIMEOUT", settlementWorkerDefaultRequestTimeout),
		OutboxDispatchInterval:        durationEnvOr("SETTLEMENT_OUTBOX_DISPATCH_INTERVAL", time.Second),
	}
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

func envOr(name string, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(name)); value != "" {
		return value
	}
	return fallback
}

func durationEnvOr(name string, fallback time.Duration) time.Duration {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	duration, err := time.ParseDuration(value)
	if err != nil || duration <= 0 {
		return fallback
	}
	return duration
}
