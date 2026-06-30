package distributedbackend

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
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
	udpEnabled, err := boolEnv("API_GATEWAY_QUILKIN_UDP_ENABLED", true)
	if err != nil {
		return Config{}, err
	}
	maxPacket, err := positiveIntEnv("API_GATEWAY_QUILKIN_MAX_PACKET_BYTES", 8192)
	if err != nil {
		return Config{}, err
	}
	workers, err := positiveIntEnv("API_GATEWAY_QUILKIN_WORKERS", 8)
	if err != nil {
		return Config{}, err
	}
	queueDepth, err := positiveIntEnv("API_GATEWAY_QUILKIN_QUEUE_DEPTH", 1024)
	if err != nil {
		return Config{}, err
	}
	ratePerSecond, err := positiveFloatEnv("API_GATEWAY_UDP_RATE_LIMIT_PER_SECOND", 50)
	if err != nil {
		return Config{}, err
	}
	rateBurst, err := positiveIntEnv("API_GATEWAY_UDP_RATE_LIMIT_BURST", 100)
	if err != nil {
		return Config{}, err
	}
	replayTTL, err := positiveDurationEnv("API_GATEWAY_UDP_REPLAY_TTL", 10*time.Minute)
	if err != nil {
		return Config{}, err
	}
	authRequired, err := boolEnv("API_GATEWAY_UDP_AUTH_REQUIRED", true)
	if err != nil {
		return Config{}, err
	}
	downstreamTimeout, err := positiveDurationEnv("API_GATEWAY_DOWNSTREAM_TIMEOUT", 5*time.Second)
	if err != nil {
		return Config{}, err
	}
	hmacKeyID, err := nonBlankEnv("API_GATEWAY_UDP_HMAC_KEY_ID", "primary")
	if err != nil {
		return Config{}, err
	}
	config := Config{
		HTTPAddr:          envOr("API_GATEWAY_HTTP_ADDR", ":8080"),
		QuilkinUDPAddr:    envOr("API_GATEWAY_QUILKIN_UDP_ADDR", ":26000"),
		QuilkinUDPEnabled: udpEnabled,
		QuilkinMaxPacket:  maxPacket,
		QuilkinWorkers:    workers,
		QuilkinQueueDepth: queueDepth,
		UDPRatePerSecond:  ratePerSecond,
		UDPRateBurst:      rateBurst,
		UDPReplayTTL:      replayTTL,
		UDPAuthRequired:   authRequired,
		UDPHMACSecret:     envOr("API_GATEWAY_UDP_HMAC_SECRET", ""),
		UDPHMACKeyID:      hmacKeyID,
		UDPPrincipalKeys:  principalKeys,
		MarketURL:         strings.TrimRight(envOr("MARKET_URL", "http://localhost:8081"), "/"),
		DownstreamTimeout: downstreamTimeout,
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
	decoder := json.NewDecoder(strings.NewReader(value))
	decoder.DisallowUnknownFields()
	opening, err := decoder.Token()
	if err != nil || opening != json.Delim('{') {
		if err == nil {
			err = fmt.Errorf("principal keyring must be a JSON object")
		}
		return nil, fmt.Errorf("parse API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON: %w", err)
	}
	credentials := make(map[string]UDPPrincipalCredential)
	capsuleerKeys := make(map[int64]string)
	for decoder.More() {
		token, err := decoder.Token()
		if err != nil {
			return nil, fmt.Errorf("parse API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON: %w", err)
		}
		keyID, ok := token.(string)
		if !ok {
			return nil, fmt.Errorf("parse API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON: key ID is not a string")
		}
		if _, duplicate := credentials[keyID]; duplicate {
			return nil, fmt.Errorf("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON contains duplicate key ID %q", keyID)
		}
		var credential UDPPrincipalCredential
		if err := decoder.Decode(&credential); err != nil {
			return nil, fmt.Errorf("parse API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON credential %q: %w", keyID, err)
		}
		if strings.TrimSpace(keyID) == "" || credential.CapsuleerID <= 0 || strings.TrimSpace(credential.Secret) == "" {
			return nil, fmt.Errorf("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON contains an invalid credential for key %q", keyID)
		}
		if previous, duplicate := capsuleerKeys[credential.CapsuleerID]; duplicate {
			return nil, fmt.Errorf("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON maps capsuleer %d to duplicate key IDs %q and %q", credential.CapsuleerID, previous, keyID)
		}
		credentials[keyID] = credential
		capsuleerKeys[credential.CapsuleerID] = keyID
	}
	if _, err := decoder.Token(); err != nil {
		return nil, fmt.Errorf("parse API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON: %w", err)
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		if err == nil {
			err = fmt.Errorf("trailing JSON data")
		}
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

func nonBlankEnv(name string, fallback string) (string, error) {
	value, set := os.LookupEnv(name)
	if !set {
		return fallback, nil
	}
	value = strings.TrimSpace(value)
	if value == "" {
		return "", fmt.Errorf("%s must not be empty", name)
	}
	return value, nil
}

func positiveDurationEnv(name string, fallback time.Duration) (time.Duration, error) {
	value, set := os.LookupEnv(name)
	if !set {
		return fallback, nil
	}
	value = strings.TrimSpace(value)
	duration, err := time.ParseDuration(value)
	if err != nil || duration <= 0 {
		return 0, fmt.Errorf("%s must be a positive duration, got %q", name, value)
	}
	return duration, nil
}

func boolEnv(name string, fallback bool) (bool, error) {
	value, set := os.LookupEnv(name)
	if !set {
		return fallback, nil
	}
	value = strings.TrimSpace(value)
	parsed, err := strconv.ParseBool(value)
	if err != nil {
		return false, fmt.Errorf("%s must be a boolean, got %q", name, value)
	}
	return parsed, nil
}

func positiveIntEnv(name string, fallback int) (int, error) {
	value, set := os.LookupEnv(name)
	if !set {
		return fallback, nil
	}
	value = strings.TrimSpace(value)
	parsed, err := strconv.Atoi(value)
	if err != nil || parsed <= 0 {
		return 0, fmt.Errorf("%s must be a positive integer, got %q", name, value)
	}
	return parsed, nil
}

func positiveFloatEnv(name string, fallback float64) (float64, error) {
	value, set := os.LookupEnv(name)
	if !set {
		return fallback, nil
	}
	value = strings.TrimSpace(value)
	parsed, err := strconv.ParseFloat(value, 64)
	if err != nil || parsed <= 0 || math.IsNaN(parsed) || math.IsInf(parsed, 0) {
		return 0, fmt.Errorf("%s must be a positive number, got %q", name, value)
	}
	return parsed, nil
}
