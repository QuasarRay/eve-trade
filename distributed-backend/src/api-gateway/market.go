package gateway

import (
	"context"
	"errors"
	"io"
	"net/http"

	"connectrpc.com/connect"
	commonv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/common/v1"
	tradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/domain/trade/v1"
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

func playerSafeTradeReference(result *MarketInteractionResult) *commonv1.TradeInstanceId {
	if result == nil || result.GetSettlementResult() == nil {
		return nil
	}

	return result.GetSettlementResult().GetTradeInstanceId()
}

func playerSafeGatewayStatus(result *MarketInteractionResult) gatewayv1.GameTradeUiActivityResultStatus {
	switch playerSafeStatus(result) {
	case "committed", "idempotent_replay":
		return gatewayv1.GameTradeUiActivityResultStatus_GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_APPLIED
	case "rejected", "rolled_back":
		return gatewayv1.GameTradeUiActivityResultStatus_GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_REJECTED
	default:
		return gatewayv1.GameTradeUiActivityResultStatus_GAME_TRADE_UI_ACTIVITY_RESULT_STATUS_RESULT_UNKNOWN
	}
}

func playerSafeRejectionCode(result *MarketInteractionResult) gatewayv1.GameTradeRejectionCode {
	if result == nil {
		return gatewayv1.GameTradeRejectionCode_GAME_TRADE_REJECTION_CODE_UNSPECIFIED
	}
	if result.GetError() != nil {
		return gatewayv1.GameTradeRejectionCode_GAME_TRADE_REJECTION_CODE_MARKET_RULE_REJECTED
	}

	settlementResult := result.GetSettlementResult()
	if settlementResult == nil {
		return gatewayv1.GameTradeRejectionCode_GAME_TRADE_REJECTION_CODE_UNSPECIFIED
	}
	if settlementResult.GetRejected() != nil || settlementResult.GetRolledBack() != nil {
		return gatewayv1.GameTradeRejectionCode_GAME_TRADE_REJECTION_CODE_SETTLEMENT_REJECTED
	}

	return gatewayv1.GameTradeRejectionCode_GAME_TRADE_REJECTION_CODE_UNSPECIFIED
}

func playerSafeResultUnknownReason(result *MarketInteractionResult) gatewayv1.GameTradeResultUnknownReason {
	if result == nil {
		return gatewayv1.GameTradeResultUnknownReason_GAME_TRADE_RESULT_UNKNOWN_REASON_MARKET_TIMEOUT
	}
	if result.GetSettlementResult() == nil {
		return gatewayv1.GameTradeResultUnknownReason_GAME_TRADE_RESULT_UNKNOWN_REASON_TRANSPORT_ERROR_AFTER_SUBMIT
	}
	if result.GetSettlementResult().GetResultUnknown() != nil {
		return gatewayv1.GameTradeResultUnknownReason_GAME_TRADE_RESULT_UNKNOWN_REASON_SETTLEMENT_TIMEOUT
	}

	return gatewayv1.GameTradeResultUnknownReason_GAME_TRADE_RESULT_UNKNOWN_REASON_UNSPECIFIED
}

func playerSafeTradeSnapshot(result *MarketInteractionResult) *gatewayv1.GameTradeInstanceResultSnapshot {
	if result == nil || result.GetSettlementResult() == nil {
		return nil
	}

	settlementResult := result.GetSettlementResult()
	if settlementResult.GetTradeInstanceId() == nil {
		return nil
	}

	return &gatewayv1.GameTradeInstanceResultSnapshot{
		TradeInstanceId: settlementResult.GetTradeInstanceId(),
		TradeState:      tradev1.TradeState(settlementResult.GetResultingTradeState()),
	}
}

func gameTradeCommandKind(kind gatewayv1.GameTradeUiActivityKind) gatewayv1.GameTradeCommandKind {
	switch kind {
	case gatewayv1.GameTradeUiActivityKind_GAME_TRADE_UI_ACTIVITY_KIND_ISSUE_BUTTON_PRESSED:
		return gatewayv1.GameTradeCommandKind_GAME_TRADE_COMMAND_KIND_ISSUE_TRADE
	case gatewayv1.GameTradeUiActivityKind_GAME_TRADE_UI_ACTIVITY_KIND_ACCEPT_BUTTON_PRESSED:
		return gatewayv1.GameTradeCommandKind_GAME_TRADE_COMMAND_KIND_ACCEPT_TRADE
	case gatewayv1.GameTradeUiActivityKind_GAME_TRADE_UI_ACTIVITY_KIND_CANCEL_BUTTON_PRESSED:
		return gatewayv1.GameTradeCommandKind_GAME_TRADE_COMMAND_KIND_CANCEL_TRADE
	default:
		return gatewayv1.GameTradeCommandKind_GAME_TRADE_COMMAND_KIND_UNSPECIFIED
	}
}
