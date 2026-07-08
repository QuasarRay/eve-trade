package market

import (
	"context"

	"encore.dev/pubsub"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
)

type SettlementPublisher interface {
	PublishSettlementWork(ctx context.Context, work *settlement.Work) (string, error)
}

type PubSubSettlementPublisher struct {
	topic pubsub.Publisher[*settlement.Work]
}

func NewSettlementPublisher() PubSubSettlementPublisher {
	return PubSubSettlementPublisher{
		topic: pubsub.TopicRef[pubsub.Publisher[*settlement.Work]](settlement.WorkTopic),
	}
}

func (p PubSubSettlementPublisher) PublishSettlementWork(ctx context.Context, work *settlement.Work) (string, error) {
	return p.topic.Publish(ctx, work)
}
