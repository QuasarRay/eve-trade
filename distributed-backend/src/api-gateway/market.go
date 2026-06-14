package gateway

import (
	"context"
	"errors"
	"io"
	"net/http"

	"connectrpc.com/connect"
	gatewayv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/gateway/v1"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/market/v1"
	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/market/v1/marketv1connect"
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/settlement/v1"
)

type MarketInteractionResult = marketv1.MarketTradeResult

type Market interface {
	SendProjectTradeInteraction(context.Context, *marketv1.ProjectTradeInteraction) (*MarketInteractionResult, error)
}

type MarketClient struct {
	client marketv1connect.MarketTradeServiceClient
}

func NewMarketClient(url string, opts ...connect.ClientOption) MarketClient {
	return MarketClient{
		client: marketv1connect.NewMarketTradeServiceClient(
			http.DefaultClient,
			url,
			opts...,
		),
	}
}

func (m MarketClient) SendProjectTradeInteraction(
	ctx context.Context,
	interaction *marketv1.ProjectTradeInteraction,
) (*MarketInteractionResult, error) {
	stream := m.client.StreamProjectTradeInteractions(ctx)

	if err := stream.Send(&marketv1.StreamProjectTradeInteractionsRequest{
		Interaction: interaction,
	}); err != nil {
		return nil, err
	}

	if err := stream.CloseRequest(); err != nil {
		return nil, err
	}

	response, err := stream.Receive()
	if err != nil {
		return nil, err
	}

	if err := stream.CloseResponse(); err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}

	return response.GetResult(), nil
}

func playerSafeStatus(result *MarketInteractionResult) string {
	if result == nil {
		return "result_unknown"
	}

	if result.GetError() != nil {
		return "rejected"
	}

	settlementResult := result.GetSettlementResult()
	if settlementResult == nil {
		return "result_unknown"
	}

	switch settlementResult.GetAttemptStatus() {
	case settlementv1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_COMMITTED:
		return "committed"
	case settlementv1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_REJECTED:
		return "rejected"
	case settlementv1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_ROLLED_BACK:
		return "rolled_back"
	case settlementv1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_RESULT_UNKNOWN:
		return "result_unknown"
	case settlementv1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_IDEMPOTENT_REPLAY:
		return "idempotent_replay"
	default:
		return "result_unknown"
	}
}

func playerSafeMessage(result *MarketInteractionResult) string {
	if result == nil {
		return "The trade request was received, but the final result is not known yet."
	}

	if result.GetError() != nil {
		return "The trade request was rejected."
	}

	settlementResult := result.GetSettlementResult()
	if settlementResult == nil {
		return "The trade result is not known yet."
	}

	if settlementResult.GetRejected() != nil {
		return "The trade request was rejected."
	}

	if settlementResult.GetRolledBack() != nil {
		return "The trade request could not be completed."
	}

	if settlementResult.GetResultUnknown() != nil {
		return "The trade result is not known yet."
	}

	return "The trade request entered the trade lifecycle."
}

func playerSafeTradeReference(result *MarketInteractionResult) string {
	if result == nil || result.GetSettlementResult() == nil {
		return ""
	}

	tradeInstanceID := result.GetSettlementResult().GetTradeInstanceId()
	if tradeInstanceID == nil {
		return ""
	}

	return tradeInstanceID.GetValue()
}

func playerSafeGatewayStatus(result *MarketInteractionResult) gatewayv1.GameTradeUiActivityResultStatus {
	switch playerSafeStatus(result) {
	case "committed", "idempotent_replay":
		return gatewayv1.GameTradeUiActivityResultStatus_GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_APPLIED
	case "rejected":
		return gatewayv1.GameTradeUiActivityResultStatus_GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_REJECTED
	case "rolled_back":
		return gatewayv1.GameTradeUiActivityResultStatus_GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_FAILED
	default:
		return gatewayv1.GameTradeUiActivityResultStatus_GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_RESULT_UNKNOWN
	}
}