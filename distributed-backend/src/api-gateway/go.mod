module github.com/astral/eve-trade/api-gateway

go 1.26

require (
	connectrpc.com/connect v1.20.0
	github.com/astral/eve-trade/market v0.0.0
	golang.org/x/net v0.55.0
	google.golang.org/protobuf v1.36.11
)

require golang.org/x/text v0.37.0 // indirect

replace github.com/astral/eve-trade/market => ../market
