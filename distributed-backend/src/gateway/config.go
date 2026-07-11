package gateway

import (
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/lainio/err2"
	"github.com/lainio/err2/try"
)

type UDPPrincipalCredential struct {
	CapsuleerID int64  `json:"capsuleer_id"`
	Secret      string `json:"secret"`
}

type Config struct {
	QuilkinUDPAddr    string
	QuilkinUDPEnabled bool
	QuilkinMaxPacket  int
	QuilkinWorkers    int
	QuilkinQueueDepth int
	UDPRatePerSecond  float64
	UDPRateBurst      int
	UDPSourceRate     float64
	UDPSourceBurst    int
	UDPLimiterMaxIDs  int
	UDPLimiterIdleTTL time.Duration
	UDPReplayTTL      time.Duration
	UDPReplayMaxIDs   int
	UDPAuthRequired   bool
	UDPHMACSecret     string
	UDPHMACKeyID      string
	UDPPrincipalKeys  map[string]UDPPrincipalCredential
	DownstreamTimeout time.Duration
}

func LoadConfig() (config Config, err error) {
	defer err2.Handle(&err, "load gateway configuration")

	config = Config{
		QuilkinUDPAddr:    envOr("API_GATEWAY_QUILKIN_UDP_ADDR", ":26000"),
		QuilkinUDPEnabled: try.To1(boolEnv("API_GATEWAY_QUILKIN_UDP_ENABLED", true)),
		QuilkinMaxPacket:  try.To1(positiveIntEnv("API_GATEWAY_QUILKIN_MAX_PACKET_BYTES", 8192)),
		QuilkinWorkers:    try.To1(positiveIntEnv("API_GATEWAY_QUILKIN_WORKERS", 8)),
		QuilkinQueueDepth: try.To1(positiveIntEnv("API_GATEWAY_QUILKIN_QUEUE_DEPTH", 1024)),
		UDPRatePerSecond:  try.To1(positiveFloatEnv("API_GATEWAY_UDP_RATE_LIMIT_PER_SECOND", 50)),
		UDPRateBurst:      try.To1(positiveIntEnv("API_GATEWAY_UDP_RATE_LIMIT_BURST", 100)),
		UDPSourceRate:     try.To1(positiveFloatEnv("API_GATEWAY_UDP_SOURCE_RATE_LIMIT_PER_SECOND", 100)),
		UDPSourceBurst:    try.To1(positiveIntEnv("API_GATEWAY_UDP_SOURCE_RATE_LIMIT_BURST", 200)),
		UDPLimiterMaxIDs:  try.To1(positiveIntEnv("API_GATEWAY_UDP_LIMITER_MAX_IDENTITIES", defaultLimiterMaxIdentities)),
		UDPLimiterIdleTTL: try.To1(positiveDurationEnv("API_GATEWAY_UDP_LIMITER_IDLE_TTL", defaultLimiterIdleTTL)),
		UDPReplayTTL:      try.To1(positiveDurationEnv("API_GATEWAY_UDP_REPLAY_TTL", 10*time.Minute)),
		UDPReplayMaxIDs:   try.To1(positiveIntEnv("API_GATEWAY_UDP_REPLAY_MAX_ENTRIES", defaultReplayMaxEntries)),
		UDPAuthRequired:   try.To1(boolEnv("API_GATEWAY_UDP_AUTH_REQUIRED", true)),
		UDPHMACSecret:     envOr("API_GATEWAY_UDP_HMAC_SECRET", ""),
		UDPHMACKeyID:      try.To1(nonBlankEnv("API_GATEWAY_UDP_HMAC_KEY_ID", "primary")),
		UDPPrincipalKeys:  try.To1(parseUDPPrincipalKeys(os.Getenv("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON"))),
		DownstreamTimeout: try.To1(positiveDurationEnv("API_GATEWAY_DOWNSTREAM_TIMEOUT", 5*time.Second)),
	}
	if config.QuilkinUDPEnabled && config.UDPAuthRequired && len(config.UDPPrincipalKeys) == 0 {
		return Config{}, fmt.Errorf("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON must define at least one authenticated capsuleer")
	}
	if config.QuilkinUDPEnabled && strings.TrimSpace(config.UDPHMACSecret) == "" {
		return Config{}, fmt.Errorf("API_GATEWAY_UDP_HMAC_SECRET is required to authenticate UDP responses")
	}
	return config, err
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
	secretKeys := make(map[[sha256.Size]byte]string)
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
		credential.Secret = strings.TrimSpace(credential.Secret)
		secretFingerprint := sha256.Sum256([]byte(credential.Secret))
		if previous, duplicate := secretKeys[secretFingerprint]; duplicate {
			return nil, fmt.Errorf("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON reuses secret material for key IDs %q and %q", previous, keyID)
		}
		credentials[keyID] = credential
		capsuleerKeys[credential.CapsuleerID] = keyID
		secretKeys[secretFingerprint] = keyID
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
