module github.com/QuasarRay/eve-trade/messaging

go 1.26

require (
	github.com/QuasarRay/eve-trade/proto v0.0.0
	github.com/rabbitmq/amqp091-go v1.10.0
	google.golang.org/protobuf v1.36.11
)

replace github.com/QuasarRay/eve-trade/proto => ../../proto
