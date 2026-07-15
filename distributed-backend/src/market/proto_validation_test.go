package market

import "testing"

func TestDecodeTradeGUIInteractionAcceptsSimulatorControlID(t *testing.T) {
	raw := []byte(`{
		"schema_version":"eve-trade-gui.v1",
		"interaction_id":"interaction-1",
		"ui":{
			"window":"regional_market",
			"control_id":"market_place_sell_order",
			"action":"market_place_sell_order"
		},
		"input":{
			"issued_by_capsuleer_id":"1001",
			"item_stack":{
				"item_stack_id":"11111111-1111-4111-8111-111111111111",
				"owner_id":"1001",
				"item_type_id":"34",
				"station_id":"60003760",
				"quantity":"10"
			},
			"quantity":"4",
			"unit_price_isk":"25"
		}
	}`)
	interaction, err := decodeTradeGUIInteraction(raw)
	if err != nil {
		t.Fatalf("decodeTradeGUIInteraction: %v", err)
	}
	if got := interaction.GetUi().GetControlId(); got != "market_place_sell_order" {
		t.Fatalf("control_id = %q", got)
	}
}
