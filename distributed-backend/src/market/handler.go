package market

type MarketHandler struct {
	settlement SettlementPublisher
	trades     TradeRepository
}

func NewMarketHandler(settlement SettlementPublisher, trades TradeRepository) *MarketHandler {
	return &MarketHandler{settlement: settlement, trades: trades}
}
