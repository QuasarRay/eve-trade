package gateway

import (
	"context"
	"errors"
	"io"
	"net/http"

	evetradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/v1"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/market/v1"
	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/market/v1/marketv1connect"
)

type MarketInteractionResult = marketv1.MarketInteractionResult

type Market interface {
	SendProjectTradeInteraction(context.Context, *evetradev1.ProjectTradeInteraction) (*MarketInteractionResult, error)
}

type MarketClient struct {
	client marketv1connect.MarketInteractionIngressServiceClient
}

func NewMarketClient(url string) MarketClient {
	return MarketClient{
		client: marketv1connect.NewMarketInteractionIngressServiceClient(
			http.DefaultClient,
			url,
		),
	}
}

func (m MarketClient) SendProjectTradeInteraction(ctx context.Context, interaction *evetradev1.ProjectTradeInteraction) (*MarketInteractionResult, error) {
	stream := m.client.StreamProjectTradeInteractions(ctx)
	if err := stream.Send(interaction); err != nil {
		return nil, err
	}
	if err := stream.CloseRequest(); err != nil {
		return nil, err
	}

	result, err := stream.Receive()
	if err != nil {
		return nil, err
	}
	if err := stream.CloseResponse(); err != nil && !errors.Is(err, io.EOF) {
		return nil, err
	}

	return result, nil
}

func playerSafeStatus(result *MarketInteractionResult) string {
	if result == nil || result.GetTransactionOutcome() == nil {
		return "result_unknown"
	}

	switch result.GetTransactionOutcome().GetAttemptStatus() {
	case evetradev1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_COMMITTED:
		return "committed"
	case evetradev1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_REJECTED:
		return "rejected"
	case evetradev1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_ROLLED_BACK:
		return "rolled_back"
	case evetradev1.TransactionAttemptStatus_TRANSACTION_ATTEMPT_STATUS_IDEMPOTENT_REPLAY:
		return "idempotent_replay"
	default:
		return "result_unknown"
	}
}

func playerSafeMessage(result *MarketInteractionResult) string {
	if result == nil || result.GetTransactionOutcome() == nil {
		return "The trade request was received, but the final result is not known yet."
	}
	if rejected := result.GetTransactionOutcome().GetRejected(); rejected != nil {
		return "The trade request was rejected."
	}
	if rolledBack := result.GetTransactionOutcome().GetRolledBack(); rolledBack != nil {
		return "The trade request could not be completed."
	}
	if unknown := result.GetTransactionOutcome().GetResultUnknown(); unknown != nil {
		return "The trade result is not known yet."
	}

	return "The trade request entered the trade lifecycle."
}

func playerSafeTradeReference(result *MarketInteractionResult) string {
	if result == nil || result.GetTransactionOutcome() == nil {
		return ""
	}

	outcome := result.GetTransactionOutcome()
	switch {
	case outcome.GetIssueTradeInstanceCommittedAsOutstanding() != nil:
		return outcome.GetIssueTradeInstanceCommittedAsOutstanding().GetTradeInstanceId().GetValue()
	case outcome.GetSettleTradeInstanceCommittedAsCompleted() != nil:
		return outcome.GetSettleTradeInstanceCommittedAsCompleted().GetTradeInstanceId().GetValue()
	case outcome.GetSettleTradeInstanceCommittedAsFailed() != nil:
		return outcome.GetSettleTradeInstanceCommittedAsFailed().GetTradeInstanceId().GetValue()
	case outcome.GetSettleTradeInstanceCommittedAsExpired() != nil:
		return outcome.GetSettleTradeInstanceCommittedAsExpired().GetTradeInstanceId().GetValue()
	case outcome.GetCancelTradeInstanceCommittedAsCancelled() != nil:
		return outcome.GetCancelTradeInstanceCommittedAsCancelled().GetTradeInstanceId().GetValue()
	default:
		return ""
	}
}
