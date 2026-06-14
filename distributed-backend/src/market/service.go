package market

import (
	"context"
	"errors"
	"io"

	"connectrpc.com/connect"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/market/v1"
	operationv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/operation/v1"
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/settlement/v1"
)

// Service implements market.v1.MarketTradeService.
type Service struct {
	settlement Settlement
}

func NewService(settlement Settlement) *Service {
	return &Service{settlement: settlement}
}

func (s *Service) StreamProjectTradeInteractions(ctx context.Context, stream *connect.BidiStream[marketv1.StreamProjectTradeInteractionsRequest, marketv1.StreamProjectTradeInteractionsResponse]) error {
	for {
		request, err := stream.Receive()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}

		result, err := s.MarketLayer1ReceivesProjectProtoTradeInteractionsFromAPIGatewayViaGRPC(ctx, request.GetInteraction())
		if err != nil {
			return err
		}
		if err := stream.Send(&marketv1.StreamProjectTradeInteractionsResponse{Result: result}); err != nil {
			return err
		}
	}
}

// @Market_Layer_1_receives_Project_Proto_trade_interactions_from_API_gateway_via_gRPC
func (s *Service) MarketLayer1ReceivesProjectProtoTradeInteractionsFromAPIGatewayViaGRPC(ctx context.Context, interaction *marketv1.ProjectTradeInteraction) (*marketv1.MarketTradeResult, error) {
	domainInput, err := MarketLayer1ConvertsProjectProtoContractIntoGameTradeDomainInputWhileShieldingLayer2FromGRPCWebAndDevOpsDetails(interaction)
	if err != nil {
		return nil, invalid(err)
	}

	decisionDraft, err := marketLayer2DeterminesRequiredTransactionFunctionNameFromTradeInstanceLifecycle(domainInput)
	if err != nil {
		return nil, invalid(err)
	}

	decision, err := MarketLayer2WritesRequiredTransactionFunctionNameAndRequiredRowIdentitiesIntoRequestMetadata(decisionDraft)
	if err != nil {
		return nil, invalid(err)
	}

	settlementResult, err := s.marketLayer3SendsTradeSettlementCommand(ctx, decision)
	if err != nil {
		return nil, err
	}

	return &marketv1.MarketTradeResult{
		InteractionId:    interaction.GetInteractionId(),
		CorrelationId:    interaction.GetCorrelationId(),
		Decision:         decision,
		SettlementResult: settlementResult,
	}, nil
}

// @Market_Layer_1_converts_Project_Proto_contract_into_game_trade_domain_input_while_shielding_Layer_2_from_gRPC_web_and_DevOps_details
func MarketLayer1ConvertsProjectProtoContractIntoGameTradeDomainInputWhileShieldingLayer2FromGRPCWebAndDevOpsDetails(interaction *marketv1.ProjectTradeInteraction) (*marketv1.ProjectTradeInteraction, error) {
	if err := validateProjectTradeInteraction(interaction); err != nil {
		return nil, err
	}

	return interaction, nil
}

func marketLayer2DeterminesRequiredTransactionFunctionNameFromTradeInstanceLifecycle(interaction *marketv1.ProjectTradeInteraction) (*tradeLifecycleDecisionDraft, error) {
	if interaction == nil {
		return nil, ErrMissingInteraction
	}

	switch interaction.GetInteractionKind() {
	case marketv1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ISSUED_VISIBLE_TRADE:
		return MarketLayer2DeterminesRequiredTransactionFunctionNameIssueTradeInstanceBasedOnTradeInstanceAbsentDerivedTradeTypeGameMechanicsAndPlayerInteraction(interaction)
	case marketv1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ACCEPTED_VISIBLE_TRADE:
		return MarketLayer2DeterminesRequiredTransactionFunctionNameSettleTradeInstanceBasedOnExistingTradeInstanceDerivedTradeTypeGameMechanicsAndTradeInstanceTradeStateOutstanding(interaction)
	case marketv1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_CANCELLED_VISIBLE_TRADE:
		return MarketLayer2DeterminesRequiredTransactionFunctionNameCancelTradeInstanceBasedOnExistingTradeInstanceDerivedTradeTypeGameMechanicsAndTradeInstanceTradeStateOutstanding(interaction)
	default:
		return nil, ErrInvalidInteractionKind
	}
}

// @Market_Layer_2_determines_required_transaction_function_name(issue_trade_instance)_based_on_trade_instance_absent_derived_trade_type_game_mechanics_and_player_interaction
func MarketLayer2DeterminesRequiredTransactionFunctionNameIssueTradeInstanceBasedOnTradeInstanceAbsentDerivedTradeTypeGameMechanicsAndPlayerInteraction(interaction *marketv1.ProjectTradeInteraction) (*tradeLifecycleDecisionDraft, error) {
	if err := validateIssueInteraction(interaction); err != nil {
		return nil, err
	}

	return newTradeLifecycleDecisionDraft(operationv1.TradeOperationKind_TRADE_OPERATION_KIND_ISSUE_TRADE_INSTANCE, interaction), nil
}

// @Market_Layer_2_determines_required_transaction_function_name(settle_trade_instance)_based_on_existing_trade_instance_derived_trade_type_game_mechanics_and_TradeInstance_TradeState(OUTSTANDING)
func MarketLayer2DeterminesRequiredTransactionFunctionNameSettleTradeInstanceBasedOnExistingTradeInstanceDerivedTradeTypeGameMechanicsAndTradeInstanceTradeStateOutstanding(interaction *marketv1.ProjectTradeInteraction) (*tradeLifecycleDecisionDraft, error) {
	if err := validateSettleInteraction(interaction); err != nil {
		return nil, err
	}

	return newTradeLifecycleDecisionDraft(operationv1.TradeOperationKind_TRADE_OPERATION_KIND_SETTLE_TRADE_INSTANCE, interaction), nil
}

// @Market_Layer_2_determines_required_transaction_function_name(cancel_trade_instance)_based_on_existing_trade_instance_derived_trade_type_game_mechanics_and_TradeInstance_TradeState(OUTSTANDING)
func MarketLayer2DeterminesRequiredTransactionFunctionNameCancelTradeInstanceBasedOnExistingTradeInstanceDerivedTradeTypeGameMechanicsAndTradeInstanceTradeStateOutstanding(interaction *marketv1.ProjectTradeInteraction) (*tradeLifecycleDecisionDraft, error) {
	if err := validateExistingTradeInteraction(interaction, "cancelled visible trade"); err != nil {
		return nil, err
	}

	return newTradeLifecycleDecisionDraft(operationv1.TradeOperationKind_TRADE_OPERATION_KIND_CANCEL_TRADE_INSTANCE, interaction), nil
}

// @Market_Layer_2_writes_required_transaction_function_name(...)_and_required_row_identities_into_request_metadata
func MarketLayer2WritesRequiredTransactionFunctionNameAndRequiredRowIdentitiesIntoRequestMetadata(draft *tradeLifecycleDecisionDraft) (*marketv1.TradeDecision, error) {
	return buildTradeDecision(draft)
}

func (s *Service) marketLayer3SendsTradeSettlementCommand(ctx context.Context, decision *marketv1.TradeDecision) (*settlementv1.TradeSettlementResult, error) {
	command, err := settlementCommandFromDecision(decision)
	if err != nil {
		return nil, invalid(err)
	}

	result, err := s.settlement.SendTradeSettlementCommand(ctx, command)
	if err != nil {
		return nil, err
	}
	if result == nil {
		return nil, connect.NewError(connect.CodeInternal, ErrMissingSettlementResult)
	}

	return result, nil
}

func invalid(err error) error {
	return connect.NewError(connect.CodeInvalidArgument, err)
}
