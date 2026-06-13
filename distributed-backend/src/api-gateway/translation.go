package gateway

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"strconv"
	"strings"
	"time"
	"unicode"

	evetradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/v1"
)

var (
	ErrMissingActivity          = errors.New("game trade UI activity is required")
	ErrMissingActivityID        = errors.New("game trade UI activity.activity_id is required")
	ErrMissingGameServerID      = errors.New("game trade UI activity.game_server_id is required")
	ErrMissingGameSessionID     = errors.New("game trade UI activity.game_session_id is required")
	ErrMissingCapsuleerID       = errors.New("game trade UI activity.capsuleer_id is required")
	ErrMissingGameUIVersion     = errors.New("game trade UI activity.game_ui_version is required")
	ErrInvalidActivityKind      = errors.New("game trade UI activity.activity_kind is invalid")
	ErrInvalidNumericFieldValue = errors.New("game UI numeric field is invalid")
)

type visibleFieldSet map[string]string

func validateGameTradeUIActivity(activity *evetradev1.GameTradeUiActivity) error {
	if activity == nil {
		return ErrMissingActivity
	}
	if activity.GetActivityId().GetValue() == "" {
		return ErrMissingActivityID
	}
	if activity.GetGameServerId().GetValue() == "" {
		return ErrMissingGameServerID
	}
	if activity.GetGameSessionId().GetValue() == "" {
		return ErrMissingGameSessionID
	}
	if activity.GetCapsuleerId().GetValue() == 0 {
		return ErrMissingCapsuleerID
	}
	if activity.GetGameUiVersion().GetValue() == "" {
		return ErrMissingGameUIVersion
	}
	if projectInteractionKind(activity.GetActivityKind()) == evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_UNSPECIFIED {
		return ErrInvalidActivityKind
	}

	return nil
}

func projectInteractionKind(kind evetradev1.GameTradeUiActivityKind) evetradev1.ProjectTradeInteractionKind {
	switch kind {
	case evetradev1.GameTradeUiActivityKind_GAME_TRADE_UI_ACTIVITY_KIND_ISSUE_BUTTON_PRESSED:
		return evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ISSUED_VISIBLE_TRADE
	case evetradev1.GameTradeUiActivityKind_GAME_TRADE_UI_ACTIVITY_KIND_ACCEPT_BUTTON_PRESSED:
		return evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ACCEPTED_VISIBLE_TRADE
	case evetradev1.GameTradeUiActivityKind_GAME_TRADE_UI_ACTIVITY_KIND_CANCEL_BUTTON_PRESSED:
		return evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_CANCELLED_VISIBLE_TRADE
	default:
		return evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_UNSPECIFIED
	}
}

func knownTradeButton(kind evetradev1.GameTradeUiActivityKind, raw string) evetradev1.KnownTradeButton {
	normalized := strings.ToLower(raw)
	switch {
	case strings.Contains(normalized, "issue"), strings.Contains(normalized, "create"), strings.Contains(normalized, "sell"), strings.Contains(normalized, "buy"):
		return evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_ISSUE
	case strings.Contains(normalized, "accept"), strings.Contains(normalized, "confirm"), strings.Contains(normalized, "fill"):
		return evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_ACCEPT
	case strings.Contains(normalized, "cancel"), strings.Contains(normalized, "withdraw"):
		return evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_CANCEL
	}

	switch kind {
	case evetradev1.GameTradeUiActivityKind_GAME_TRADE_UI_ACTIVITY_KIND_ISSUE_BUTTON_PRESSED:
		return evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_ISSUE
	case evetradev1.GameTradeUiActivityKind_GAME_TRADE_UI_ACTIVITY_KIND_ACCEPT_BUTTON_PRESSED:
		return evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_ACCEPT
	case evetradev1.GameTradeUiActivityKind_GAME_TRADE_UI_ACTIVITY_KIND_CANCEL_BUTTON_PRESSED:
		return evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_CANCEL
	default:
		return evetradev1.KnownTradeButton_KNOWN_TRADE_BUTTON_UNSPECIFIED
	}
}

func knownTradeWindow(raw string, fields visibleFieldSet) evetradev1.KnownTradeWindow {
	value := strings.ToLower(raw + " " + fields.first("tradewindow", "window", "screen"))
	switch {
	case strings.Contains(value, "auction"):
		return evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_AUCTION_WINDOW
	case strings.Contains(value, "direct"):
		return evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_DIRECT_TRADE_WINDOW
	case strings.Contains(value, "contract"):
		return evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_CONTRACT_WINDOW
	case strings.Contains(value, "market"), value == " ":
		return evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_MARKET_WINDOW
	default:
		return evetradev1.KnownTradeWindow_KNOWN_TRADE_WINDOW_MARKET_WINDOW
	}
}

func occurredAtUnixMillis(activity *evetradev1.GameTradeUiActivity) int64 {
	if activity.GetOccurredAtUnixMillis() > 0 {
		return activity.GetOccurredAtUnixMillis()
	}

	return time.Now().UnixMilli()
}

func newVisibleFieldSet(fields []*evetradev1.GameTradeUiField) visibleFieldSet {
	out := make(visibleFieldSet, len(fields))
	for _, field := range fields {
		key := normalizeFieldName(field.GetRawGameFieldName())
		if key == "" {
			continue
		}
		out[key] = strings.TrimSpace(field.GetRawGameFieldValue())
	}

	return out
}

func normalizeFieldName(value string) string {
	var builder strings.Builder
	for _, r := range strings.ToLower(value) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) {
			builder.WriteRune(r)
		}
	}

	return builder.String()
}

func (f visibleFieldSet) first(names ...string) string {
	for _, name := range names {
		if value := f[normalizeFieldName(name)]; value != "" {
			return value
		}
	}

	return ""
}

func (f visibleFieldSet) int64(names ...string) (int64, bool, error) {
	value := f.first(names...)
	if value == "" {
		return 0, false, nil
	}

	parsed, err := strconv.ParseInt(normalizeIntegerFieldValue(value), 10, 64)
	if err != nil {
		return 0, true, fmt.Errorf("%w: %q", ErrInvalidNumericFieldValue, value)
	}

	return parsed, true, nil
}

func normalizeIntegerFieldValue(value string) string {
	replacer := strings.NewReplacer(",", "", "_", "", " ", "", "\t", "", "\n", "", "\r", "", "\u00a0", "")
	return replacer.Replace(value)
}

func (f visibleFieldSet) typedValues() (*evetradev1.PlayerTypedTradeValues, error) {
	quantity, _, err := f.int64("quantity", "itemquantity", "qty")
	if err != nil {
		return nil, err
	}
	unitPrice, _, err := f.int64("unitpriceisk", "unitprice", "price")
	if err != nil {
		return nil, err
	}
	totalPrice, _, err := f.int64("totalpriceisk", "totalprice", "total")
	if err != nil {
		return nil, err
	}

	values := &evetradev1.PlayerTypedTradeValues{}
	if quantity > 0 {
		values.Quantity = &evetradev1.ItemQuantity{Value: quantity}
	}
	if unitPrice > 0 {
		values.UnitPrice = &evetradev1.IskAmount{Value: unitPrice}
	}
	if totalPrice > 0 {
		values.TotalPrice = &evetradev1.IskAmount{Value: totalPrice}
	}
	if totalPrice == 0 && unitPrice > 0 && quantity > 0 {
		values.TotalPrice = &evetradev1.IskAmount{Value: unitPrice * quantity}
	}

	return values, nil
}

func (f visibleFieldSet) selectedItems(values *evetradev1.PlayerTypedTradeValues) ([]*evetradev1.PlayerSelectedItem, error) {
	itemTypeID, hasItemTypeID, err := f.int64("itemtypeid", "typeid", "selecteditemtypeid")
	if err != nil {
		return nil, err
	}
	if !hasItemTypeID {
		return nil, nil
	}

	quantity := values.GetQuantity().GetValue()
	itemQuantity, hasItemQuantity, err := f.int64("selectedquantity", "itemquantity", "quantity", "qty")
	if err != nil {
		return nil, err
	}
	if hasItemQuantity {
		quantity = itemQuantity
	}

	selected := &evetradev1.PlayerSelectedItem{
		ItemTypeId: &evetradev1.ItemTypeId{Value: itemTypeID},
		Quantity:   &evetradev1.ItemQuantity{Value: quantity},
	}
	if stackID := f.first("itemstackid", "stackid", "selecteditemstackid"); stackID != "" {
		selected.ItemStackId = &evetradev1.ItemStackId{Value: stackID}
	}
	for _, itemInstanceID := range splitIDs(f.first("iteminstanceid", "iteminstanceids", "selectediteminstanceid")) {
		selected.ItemInstanceIds = append(selected.ItemInstanceIds, &evetradev1.ItemInstanceId{Value: itemInstanceID})
	}

	return []*evetradev1.PlayerSelectedItem{selected}, nil
}

func (f visibleFieldSet) tradeHubID() *evetradev1.TradeHubId {
	if value := f.first("tradehubid", "hubid", "stationid", "structureid", "locationid"); value != "" {
		return &evetradev1.TradeHubId{Value: value}
	}

	return nil
}

func (f visibleFieldSet) tradeInstanceID() *evetradev1.TradeInstanceId {
	if value := f.first("tradeinstanceid", "visibletradeinstanceid", "tradeid", "orderid"); value != "" {
		return &evetradev1.TradeInstanceId{Value: value}
	}

	return nil
}

func splitIDs(value string) []string {
	if value == "" {
		return nil
	}

	return strings.FieldsFunc(value, func(r rune) bool {
		return r == ',' || r == ';' || unicode.IsSpace(r)
	})
}

func stableID(prefix string, values ...string) string {
	hash := sha256.New()
	writeStableText(hash, prefix)
	for _, value := range values {
		writeStableText(hash, value)
	}
	sum := hash.Sum(nil)
	return prefix + "-" + hex.EncodeToString(sum[:16])
}

func writeStableText(hash interface{ Write([]byte) (int, error) }, value string) {
	_, _ = hash.Write([]byte{byte(len(value) >> 24), byte(len(value) >> 16), byte(len(value) >> 8), byte(len(value))})
	_, _ = hash.Write([]byte(value))
}
