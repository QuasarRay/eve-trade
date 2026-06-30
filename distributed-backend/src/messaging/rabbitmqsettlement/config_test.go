package rabbitmqsettlement

import (
	"testing"
	"time"
)

func TestConfigWithDefaultsFillsEveryInvalidOrEmptyValue(t *testing.T) {
	got := (Config{RequestTimeout: -1, PublishTimeout: -1, PrefetchCount: -1}).WithDefaults()
	if got.URL != DefaultURL || got.Exchange != DefaultExchange || got.CommandQueue != DefaultCommandQueue || got.RoutingKey != DefaultRoutingKey {
		t.Fatalf("primary topology defaults were not applied: %+v", got)
	}
	if got.DeadLetterExchange != DefaultDeadLetterExchange || got.DeadLetterQueue != DefaultDeadLetterQueue || got.DeadLetterRoutingKey != DefaultDeadLetterRoutingKey {
		t.Fatalf("dead-letter defaults were not applied: %+v", got)
	}
	if got.RequestTimeout != DefaultRequestTimeout || got.PublishTimeout != DefaultPublishTimeout || got.PrefetchCount != DefaultPrefetchCount {
		t.Fatalf("runtime defaults were not applied: %+v", got)
	}
}

func TestConfigWithDefaultsPreservesExplicitValues(t *testing.T) {
	want := Config{
		URL: "amqp://custom", Exchange: "exchange", CommandQueue: "queue", RoutingKey: "route",
		DeadLetterExchange: "dlx", DeadLetterQueue: "dlq", DeadLetterRoutingKey: "dead",
		RequestTimeout: time.Second, PublishTimeout: 2 * time.Second, PrefetchCount: 3,
	}
	if got := want.WithDefaults(); got != want {
		t.Fatalf("explicit config changed: got %+v want %+v", got, want)
	}
}
