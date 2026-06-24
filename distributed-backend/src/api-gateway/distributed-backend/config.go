package distributedbackend

import (
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	HTTPAddr          string
	QuilkinUDPAddr    string
	QuilkinUDPEnabled bool
	QuilkinMaxPacket  int
	MarketURL         string
	DownstreamTimeout time.Duration
}

func LoadConfig() Config {
	return Config{
		HTTPAddr:          envOr("API_GATEWAY_HTTP_ADDR", ":8080"),
		QuilkinUDPAddr:    envOr("API_GATEWAY_QUILKIN_UDP_ADDR", ":26000"),
		QuilkinUDPEnabled: boolEnvOr("API_GATEWAY_QUILKIN_UDP_ENABLED", true),
		QuilkinMaxPacket:  intEnvOr("API_GATEWAY_QUILKIN_MAX_PACKET_BYTES", 8192),
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

func boolEnvOr(name string, fallback bool) bool {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.ParseBool(value)
	if err != nil {
		return fallback
	}
	return parsed
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
