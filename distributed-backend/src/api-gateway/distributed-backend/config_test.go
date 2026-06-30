package distributedbackend

import (
	"strings"
	"testing"
)

func TestLoadConfigRequiresPrincipalKeyringWhenUDPAuthenticationIsEnabled(t *testing.T) {
	t.Setenv("API_GATEWAY_QUILKIN_UDP_ENABLED", "true")
	t.Setenv("API_GATEWAY_UDP_AUTH_REQUIRED", "true")
	t.Setenv("API_GATEWAY_UDP_HMAC_SECRET", "response-secret")
	t.Setenv("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON", "")

	if _, err := LoadConfig(); err == nil {
		t.Fatal("LoadConfig succeeded without an authenticated principal keyring")
	}
}

func TestLoadConfigParsesAuthenticatedPrincipalKeyring(t *testing.T) {
	t.Setenv("API_GATEWAY_QUILKIN_UDP_ENABLED", "true")
	t.Setenv("API_GATEWAY_UDP_AUTH_REQUIRED", "true")
	t.Setenv("API_GATEWAY_UDP_HMAC_SECRET", "response-secret")
	t.Setenv("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON", `{"seller":{"capsuleer_id":1001,"secret":"seller-secret"}}`)

	config, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig returned error: %v", err)
	}
	credential := config.UDPPrincipalKeys["seller"]
	if credential.CapsuleerID != 1001 || credential.Secret != "seller-secret" {
		t.Fatalf("parsed credential = %+v", credential)
	}
}

func TestLoadConfigRequiresResponseSigningSecretWhenUDPIsEnabled(t *testing.T) {
	t.Setenv("API_GATEWAY_QUILKIN_UDP_ENABLED", "true")
	t.Setenv("API_GATEWAY_UDP_AUTH_REQUIRED", "true")
	t.Setenv("API_GATEWAY_UDP_HMAC_SECRET", "")
	t.Setenv("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON", `{"seller":{"capsuleer_id":1001,"secret":"seller-secret"}}`)

	if _, err := LoadConfig(); err == nil || !strings.Contains(err.Error(), "HMAC_SECRET") {
		t.Fatalf("LoadConfig error = %v, want missing response signing secret", err)
	}
}

func TestLoadConfigRejectsMalformedOrIncompletePrincipalKeyring(t *testing.T) {
	for name, value := range map[string]string{
		"malformed":              `{`,
		"blank key":              `{"":{"capsuleer_id":1001,"secret":"secret"}}`,
		"missing id":             `{"seller":{"secret":"secret"}}`,
		"no secret":              `{"seller":{"capsuleer_id":1001}}`,
		"duplicate key id":       `{"seller":{"capsuleer_id":1001,"secret":"one"},"seller":{"capsuleer_id":2002,"secret":"two"}}`,
		"duplicate capsuleer":    `{"seller-old":{"capsuleer_id":1001,"secret":"one"},"seller-new":{"capsuleer_id":1001,"secret":"two"}}`,
		"unknown credential key": `{"seller":{"capsuleer_id":1001,"secret":"one","disabled":false}}`,
		"trailing json":          `{"seller":{"capsuleer_id":1001,"secret":"one"}} []`,
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := parseUDPPrincipalKeys(value); err == nil {
				t.Fatalf("parseUDPPrincipalKeys(%q) succeeded", value)
			}
		})
	}
}

func TestLoadConfigRejectsEveryMalformedTypedEnvironmentValue(t *testing.T) {
	validKeyring := `{"seller":{"capsuleer_id":1001,"secret":"seller-secret"}}`
	tests := []struct {
		name  string
		value string
	}{
		{name: "API_GATEWAY_QUILKIN_UDP_ENABLED", value: "truthy"},
		{name: "API_GATEWAY_UDP_AUTH_REQUIRED", value: "sometimes"},
		{name: "API_GATEWAY_QUILKIN_MAX_PACKET_BYTES", value: "0"},
		{name: "API_GATEWAY_QUILKIN_WORKERS", value: "-1"},
		{name: "API_GATEWAY_QUILKIN_QUEUE_DEPTH", value: "many"},
		{name: "API_GATEWAY_UDP_RATE_LIMIT_PER_SECOND", value: "NaN"},
		{name: "API_GATEWAY_UDP_RATE_LIMIT_BURST", value: "0"},
		{name: "API_GATEWAY_UDP_REPLAY_TTL", value: "forever"},
		{name: "API_GATEWAY_DOWNSTREAM_TIMEOUT", value: "0s"},
		{name: "API_GATEWAY_UDP_HMAC_KEY_ID", value: ""},
	}
	for _, test := range tests {
		t.Run(test.name+"="+test.value, func(t *testing.T) {
			t.Setenv("API_GATEWAY_UDP_HMAC_SECRET", "response-secret")
			t.Setenv("API_GATEWAY_UDP_PRINCIPAL_KEYS_JSON", validKeyring)
			t.Setenv(test.name, test.value)
			if _, err := LoadConfig(); err == nil || !strings.Contains(err.Error(), test.name) {
				t.Fatalf("LoadConfig error = %v, want strict %s error", err, test.name)
			}
		})
	}
}

func TestLoadConfigAcceptsExplicitFalseButNotZeroForPositiveLimits(t *testing.T) {
	t.Setenv("API_GATEWAY_QUILKIN_UDP_ENABLED", "false")
	t.Setenv("API_GATEWAY_UDP_AUTH_REQUIRED", "false")
	config, err := LoadConfig()
	if err != nil {
		t.Fatalf("LoadConfig returned error for explicit false: %v", err)
	}
	if config.QuilkinUDPEnabled || config.UDPAuthRequired {
		t.Fatalf("explicit false values were replaced by defaults: %+v", config)
	}
}
