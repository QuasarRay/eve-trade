package rabbitmqsettlement

import "time"

const (
	DefaultURL                  = "amqp://guest:guest@localhost:5672/"
	DefaultExchange             = "eve.trade.settlement"
	DefaultCommandQueue         = "eve.trade.settlement.commands"
	DefaultRoutingKey           = "settlement.execute"
	DefaultDeadLetterExchange   = "eve.trade.settlement.dlx"
	DefaultDeadLetterQueue      = "eve.trade.settlement.dead"
	DefaultDeadLetterRoutingKey = "settlement.dead"
	DefaultRequestTimeout       = 10 * time.Second
	DefaultPublishTimeout       = 5 * time.Second
	DefaultPrefetchCount        = 8
)

type Config struct {
	URL                  string
	Exchange             string
	CommandQueue         string
	RoutingKey           string
	DeadLetterExchange   string
	DeadLetterQueue      string
	DeadLetterRoutingKey string
	RequestTimeout       time.Duration
	PublishTimeout       time.Duration
	PrefetchCount        int
}

func (c Config) WithDefaults() Config {
	if c.URL == "" {
		c.URL = DefaultURL
	}
	if c.Exchange == "" {
		c.Exchange = DefaultExchange
	}
	if c.CommandQueue == "" {
		c.CommandQueue = DefaultCommandQueue
	}
	if c.RoutingKey == "" {
		c.RoutingKey = DefaultRoutingKey
	}
	if c.DeadLetterExchange == "" {
		c.DeadLetterExchange = DefaultDeadLetterExchange
	}
	if c.DeadLetterQueue == "" {
		c.DeadLetterQueue = DefaultDeadLetterQueue
	}
	if c.DeadLetterRoutingKey == "" {
		c.DeadLetterRoutingKey = DefaultDeadLetterRoutingKey
	}
	if c.RequestTimeout <= 0 {
		c.RequestTimeout = DefaultRequestTimeout
	}
	if c.PublishTimeout <= 0 {
		c.PublishTimeout = DefaultPublishTimeout
	}
	if c.PrefetchCount <= 0 {
		c.PrefetchCount = DefaultPrefetchCount
	}
	return c
}
