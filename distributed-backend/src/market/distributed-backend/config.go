package distributedbackend

import (
	"os"
	"strings"
	"time"
)

type Config struct {
	HTTPAddr                 string
	TradeSettlementURL       string
	DatabaseURL              string
	SettlementRequestTimeout time.Duration
}

func LoadConfig() Config {
	return Config{
		HTTPAddr:                 envOr("MARKET_HTTP_ADDR", ":8081"),
		TradeSettlementURL:       trimRightSlash(envOr("TRADE_SETTLEMENT_URL", "http://localhost:9090")),
		DatabaseURL:              envOr("DATABASE_URL", "postgres://postgres:postgres@localhost:5432/eve_trade"),
		SettlementRequestTimeout: durationEnvOr("MARKET_SETTLEMENT_REQUEST_TIMEOUT", 10*time.Second),
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
