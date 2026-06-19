package distributedbackend

import (
	"os"
	"strings"
	"time"
)

type Config struct {
	HTTPAddr          string
	MarketURL         string
	DownstreamTimeout time.Duration
}

func LoadConfig() Config {
	return Config{
		HTTPAddr:          envOr("API_GATEWAY_HTTP_ADDR", ":8080"),
		MarketURL:         strings.TrimRight(envOr("MARKET_URL", "http://localhost:8081"), "/"),
		DownstreamTimeout: durationEnvOr("API_GATEWAY_DOWNSTREAM_TIMEOUT", 5*time.Second),
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
