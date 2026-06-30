package distributedbackend

import (
	"encoding/json"
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

type UDPPrincipalCredential struct {
	CapsuleerID int64  `json:"capsuleer_id"`
	Secret      string `json:"secret"`
}

type Config struct {
	HTTPAddr          string
	QuilkinUDPAddr    string
	QuilkinUDPEnabled bool
	QuilkinMaxPacket  int
	QuilkinWorkers    int
	QuilkinQueueDepth int
	UDPRatePerSecond  float64
	UDPRateBurst      int
	UDPReplayTTL      time.Duration
	UDPAuthRequired   bool
	UDPHMACSecret     string
	UDPHMACKeyID      string
	UDPPrincipalKeys  map[string]UDPPrincipalCredential
	MarketURL         string
	DownstreamTimeout time.Duration
}

func LoadConfig() (Config, error) {
	principalKeys, err := parseUDPPrincipalKeys(os.Getenv("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON"))
	if err != nil {
		return Config{}, err
	}
	config := Config{
		HTTPAddr:          envOr("API_GATEWAY_HTTP_ADDR", ":8080"),
		QuilkinUDPAddr:    envOr("API_GATEWAY_QUILKIN_UDP_ADDR", ":26000"),
		QuilkinUDPEnabled: boolEnvOr("API_GATEWAY_QUILKIN_UDP_ENABLED", true),
		QuilkinMaxPacket:  intEnvOr("API_GATEWAY_QUILKIN_MAX_PACKET_BYTES", 8192),
		QuilkinWorkers:    intEnvOr("API_GATEWAY_QUILKIN_WORKERS", 8),
		QuilkinQueueDepth: intEnvOr("API_GATEWAY_QUILKIN_QUEUE_DEPTH", 1024),
		UDPRatePerSecond:  floatEnvOr("API_GATEWAY_UDP_RATE_LIMIT_PER_SECOND", 50),
		UDPRateBurst:      intEnvOr("API_GATEWAY_UDP_RATE_LIMIT_BURST", 100),
		UDPReplayTTL:      durationEnvOr("API_GATEWAY_UDP_REPLAY_TTL", 10*time.Minute),
		UDPAuthRequired:   boolEnvOr("API_GATEWAY_UDP_AUTH_REQUIRED", true),
		UDPHMACSecret:     envOr("API_GATEWAY_UDP_HMAC_SECRET", ""),
		UDPHMACKeyID:      envOr("API_GATEWAY_UDP_HMAC_KEY_ID", "primary"),
		UDPPrincipalKeys:  principalKeys,
		MarketURL:         strings.TrimRight(envOr("MARKET_URL", "http://localhost:8081"), "/"),
		DownstreamTimeout: durationEnvOr("API_GATEWAY_DOWNSTREAM_TIMEOUT", 5*time.Second),
	}
	if config.QuilkinUDPEnabled && config.UDPAuthRequired && len(config.UDPPrincipalKeys) == 0 {
		return Config{}, fmt.Errorf("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON must define at least one authenticated capsuleer")
	}
	if config.QuilkinUDPEnabled && strings.TrimSpace(config.UDPHMACSecret) == "" {
		return Config{}, fmt.Errorf("API_GATEWAY_UDP_HMAC_SECRET is required to authenticate UDP responses")
	}
	return config, nil
}

func parseUDPPrincipalKeys(value string) (map[string]UDPPrincipalCredential, error) {
	value = strings.TrimSpace(value)
	if value == "" {
		return nil, nil
	}
	var credentials map[string]UDPPrincipalCredential
	if err := json.Unmarshal([]byte(value), &credentials); err != nil {
		return nil, fmt.Errorf("parse API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON: %w", err)
	}
	for keyID, credential := range credentials {
		if strings.TrimSpace(keyID) == "" || credential.CapsuleerID <= 0 || strings.TrimSpace(credential.Secret) == "" {
			return nil, fmt.Errorf("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON contains an invalid credential for key %q", keyID)
		}
	}
	return credentials, nil
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

func floatEnvOr(name string, fallback float64) float64 {
	value := strings.TrimSpace(os.Getenv(name))
	if value == "" {
		return fallback
	}
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil || parsed <= 0 {
		return fallback
	}
	return parsed
}
