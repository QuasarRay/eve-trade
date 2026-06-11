package market

import (
	"errors"
	"sync"

	tradev1 "github.com/QuasarRay/eve-trade/distributed-backend/gen/go/trade/v1"
)

var ErrOrderNotFound = errors.New("order not found")

type Book struct {
	mu     sync.RWMutex
	orders map[string]*tradev1.MarketOrder
}

func NewBook() *Book { return &Book{orders: map[string]*tradev1.MarketOrder{}} }

func (b *Book) Save(order *tradev1.MarketOrder) *tradev1.MarketOrder {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.orders[order.OrderId] = order
	return order
}

func (b *Book) Get(id string) (*tradev1.MarketOrder, error) {
	b.mu.RLock()
	defer b.mu.RUnlock()
	order := b.orders[id]
	if order == nil {
		return nil, ErrOrderNotFound
	}
	return order, nil
}

func (b *Book) List(region, itemType string, side tradev1.OrderSide) []*tradev1.MarketOrder {
	b.mu.RLock()
	defer b.mu.RUnlock()
	orders := make([]*tradev1.MarketOrder, 0, len(b.orders))
	for _, order := range b.orders {
		if order.State != tradev1.TradeState_TRADE_STATE_OUTSTANDING {
			continue
		}
		if region != "" && order.RegionId != region || itemType != "" && order.ItemTypeId != itemType || side != tradev1.OrderSide_ORDER_SIDE_UNSPECIFIED && order.Side != side {
			continue
		}
		orders = append(orders, order)
	}
	return orders
}
