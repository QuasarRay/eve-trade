package gateway

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"unicode/utf8"
)

const (
	maxJSONNestingDepth = 64
	maxUDPPayloadBytes  = 65_507
)

// decodeStrictJSON preserves numbers while rejecting ambiguous or
// resource-exhausting JSON before authentication or canonicalization.
func decodeStrictJSON(body []byte) (any, error) {
	if len(body) == 0 {
		return nil, fmt.Errorf("JSON value is empty")
	}
	if len(body) > maxUDPPayloadBytes {
		return nil, fmt.Errorf("JSON value exceeds %d byte UDP payload limit", maxUDPPayloadBytes)
	}
	if !utf8.Valid(body) {
		return nil, fmt.Errorf("JSON value contains malformed UTF-8")
	}

	decoder := json.NewDecoder(bytes.NewReader(body))
	decoder.UseNumber()
	first, err := decoder.Token()
	if err != nil {
		return nil, err
	}
	value, err := decodeJSONToken(decoder, first, 0)
	if err != nil {
		return nil, err
	}
	if trailing, err := decoder.Token(); err != io.EOF {
		if err == nil {
			return nil, fmt.Errorf("JSON value has trailing token %v", trailing)
		}
		return nil, err
	}
	return value, nil
}

func decodeJSONToken(decoder *json.Decoder, token json.Token, depth int) (any, error) {
	delim, compound := token.(json.Delim)
	if !compound {
		return token, nil
	}
	if depth >= maxJSONNestingDepth {
		return nil, fmt.Errorf("JSON nesting exceeds maximum depth %d", maxJSONNestingDepth)
	}

	switch delim {
	case '{':
		object := make(map[string]any)
		for decoder.More() {
			keyToken, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			key, ok := keyToken.(string)
			if !ok {
				return nil, fmt.Errorf("JSON object key is not a string")
			}
			if _, exists := object[key]; exists {
				return nil, fmt.Errorf("JSON object contains duplicate key %q", key)
			}
			valueToken, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			value, err := decodeJSONToken(decoder, valueToken, depth+1)
			if err != nil {
				return nil, err
			}
			object[key] = value
		}
		closing, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		if closing != json.Delim('}') {
			return nil, fmt.Errorf("JSON object has invalid closing token %v", closing)
		}
		return object, nil

	case '[':
		array := make([]any, 0)
		for decoder.More() {
			valueToken, err := decoder.Token()
			if err != nil {
				return nil, err
			}
			value, err := decodeJSONToken(decoder, valueToken, depth+1)
			if err != nil {
				return nil, err
			}
			array = append(array, value)
		}
		closing, err := decoder.Token()
		if err != nil {
			return nil, err
		}
		if closing != json.Delim(']') {
			return nil, fmt.Errorf("JSON array has invalid closing token %v", closing)
		}
		return array, nil
	default:
		return nil, fmt.Errorf("unexpected JSON delimiter %q", delim)
	}
}
