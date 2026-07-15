package market

import (
	"os"
	"strings"
	"time"
)

type Config struct {
	DatabaseURL              string
	TradeSettlementTarget    string
	TradeSettlementTimeout   time.Duration
	StartupDependencyTimeout time.Duration
	StartupRetryInterval     time.Duration
}

func LoadConfig() Config {
	return Config{
		DatabaseURL:              envOr("MARKET_DATABASE_URL", "postgres://postgres:postgres@localhost:5432/eve_trade"),
		TradeSettlementTarget:    envOr("TRADE_SETTLEMENT_GRPC_TARGET", "127.0.0.1:9092"),
		TradeSettlementTimeout:   durationEnvOr("SETTLEMENT_WORKER_REQUEST_TIMEOUT", 10*time.Second),
		StartupDependencyTimeout: durationEnvOr("MARKET_STARTUP_DEPENDENCY_TIMEOUT", 90*time.Second),
		StartupRetryInterval:     durationEnvOr("MARKET_STARTUP_RETRY_INTERVAL", 2*time.Second),
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
	if err != nil {
		return fallback
	}
	return duration
}
