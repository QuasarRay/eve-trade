package settlementworker

import (
	"os"
	"strings"
	"time"
)

type Config struct {
	TradeSettlementTarget string
	RequestTimeout        time.Duration
}

func LoadConfig() Config {
	return Config{
		TradeSettlementTarget: envOr("TRADE_SETTLEMENT_GRPC_TARGET", "127.0.0.1:9092"),
		RequestTimeout:        durationEnvOr("SETTLEMENT_WORKER_REQUEST_TIMEOUT", 10*time.Second),
	}
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
