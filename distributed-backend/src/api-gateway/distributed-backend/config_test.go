package distributedbackend

import "testing"

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

func TestLoadConfigRejectsMalformedOrIncompletePrincipalKeyring(t *testing.T) {
	for name, value := range map[string]string{
		"malformed":  `{`,
		"blank key":  `{"":{"capsuleer_id":1001,"secret":"secret"}}`,
		"missing id": `{"seller":{"secret":"secret"}}`,
		"no secret":  `{"seller":{"capsuleer_id":1001}}`,
	} {
		t.Run(name, func(t *testing.T) {
			if _, err := parseUDPPrincipalKeys(value); err == nil {
				t.Fatalf("parseUDPPrincipalKeys(%q) succeeded", value)
			}
		})
	}
}
