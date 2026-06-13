package market

import (
	"context"
	"errors"
	"io"

	"connectrpc.com/connect"
	evetradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/v1"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/market/v1"
	settlementv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/settlement/v1"
	traderulesv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/trade_rules/v1"
)

// Service implements market.v1.MarketInteractionIngressService.
type Service struct {
	settlement Settlement
}

func NewService(settlement Settlement) *Service {
	return &Service{settlement: settlement}
}

func (s *Service) StreamProjectTradeInteractions(ctx context.Context, stream *connect.BidiStream[evetradev1.ProjectTradeInteraction, marketv1.MarketInteractionResult]) error {
	for {
		interaction, err := stream.Receive()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}

		result, err := s.MarketLayer1ReceivesProjectProtoTradeInteractionsFromAPIGatewayViaGRPC(ctx, interaction)
		if err != nil {
			return err
		}
		if err := stream.Send(result); err != nil {
			return err
		}
	}
}

// @Market_Layer_1_receives_Project_Proto_trade_interactions_from_API_gateway_via_gRPC
func (s *Service) MarketLayer1ReceivesProjectProtoTradeInteractionsFromAPIGatewayViaGRPC(ctx context.Context, interaction *evetradev1.ProjectTradeInteraction) (*marketv1.MarketInteractionResult, error) {
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

	outcome, err := s.marketLayer3SendsTradeInstanceTransactionRequestToTradeSettlementViaGRPCUsingTheSameTransactionFunctionNameChosenByLayer2(ctx, decision)
	if err != nil {
		return nil, err
	}

	return &marketv1.MarketInteractionResult{
		InteractionId:      interaction.GetInteractionId(),
		CorrelationId:      interaction.GetCorrelationId(),
		TransactionOutcome: outcome,
	}, nil
}

// @Market_Layer_1_converts_Project_Proto_contract_into_game_trade_domain_input_while_shielding_Layer_2_from_gRPC_web_and_DevOps_details
func MarketLayer1ConvertsProjectProtoContractIntoGameTradeDomainInputWhileShieldingLayer2FromGRPCWebAndDevOpsDetails(interaction *evetradev1.ProjectTradeInteraction) (*traderulesv1.GameTradeDomainInput, error) {
	if err := validateProjectTradeInteraction(interaction); err != nil {
		return nil, err
	}

	return &traderulesv1.GameTradeDomainInput{
		Interaction: interaction,
	}, nil
}

func marketLayer2DeterminesRequiredTransactionFunctionNameFromTradeInstanceLifecycle(input *traderulesv1.GameTradeDomainInput) (*tradeLifecycleDecisionDraft, error) {
	interaction, err := interactionFromDomainInput(input)
	if err != nil {
		return nil, err
	}

	switch interaction.GetInteractionKind() {
	case evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ISSUED_VISIBLE_TRADE:
		return MarketLayer2DeterminesRequiredTransactionFunctionNameIssueTradeInstanceBasedOnTradeInstanceAbsentDerivedTradeTypeGameMechanicsAndPlayerInteraction(input)
	case evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_ACCEPTED_VISIBLE_TRADE:
		return MarketLayer2DeterminesRequiredTransactionFunctionNameSettleTradeInstanceBasedOnExistingTradeInstanceDerivedTradeTypeGameMechanicsAndTradeInstanceTradeStateOutstanding(input)
	case evetradev1.ProjectTradeInteractionKind_PROJECT_TRADE_INTERACTION_KIND_PLAYER_CANCELLED_VISIBLE_TRADE:
		return MarketLayer2DeterminesRequiredTransactionFunctionNameCancelTradeInstanceBasedOnExistingTradeInstanceDerivedTradeTypeGameMechanicsAndTradeInstanceTradeStateOutstanding(input)
	default:
		return nil, ErrInvalidInteractionKind
	}
}

func interactionFromDomainInput(input *traderulesv1.GameTradeDomainInput) (*evetradev1.ProjectTradeInteraction, error) {
	if input == nil || input.GetInteraction() == nil {
		return nil, ErrMissingInteraction
	}

	return input.GetInteraction(), nil
}

// @Market_Layer_2_determines_required_transaction_function_name(issue_trade_instance)_based_on_trade_instance_absent_derived_trade_type_game_mechanics_and_player_interaction
func MarketLayer2DeterminesRequiredTransactionFunctionNameIssueTradeInstanceBasedOnTradeInstanceAbsentDerivedTradeTypeGameMechanicsAndPlayerInteraction(input *traderulesv1.GameTradeDomainInput) (*tradeLifecycleDecisionDraft, error) {
	interaction, err := interactionFromDomainInput(input)
	if err != nil {
		return nil, err
	}
	if err := validateIssueInteraction(interaction); err != nil {
		return nil, err
	}

	return newTradeLifecycleDecisionDraft(transactionFunctionIssueTradeInstance, interaction), nil
}

// @Market_Layer_2_determines_required_transaction_function_name(settle_trade_instance)_based_on_existing_trade_instance_derived_trade_type_game_mechanics_and_TradeInstance_TradeState(OUTSTANDING)
func MarketLayer2DeterminesRequiredTransactionFunctionNameSettleTradeInstanceBasedOnExistingTradeInstanceDerivedTradeTypeGameMechanicsAndTradeInstanceTradeStateOutstanding(input *traderulesv1.GameTradeDomainInput) (*tradeLifecycleDecisionDraft, error) {
	interaction, err := interactionFromDomainInput(input)
	if err != nil {
		return nil, err
	}
	if err := validateExistingTradeInteraction(interaction, "accepted visible trade"); err != nil {
		return nil, err
	}

	return newTradeLifecycleDecisionDraft(transactionFunctionSettleTradeInstance, interaction), nil
}

// @Market_Layer_2_determines_required_transaction_function_name(cancel_trade_instance)_based_on_existing_trade_instance_derived_trade_type_game_mechanics_and_TradeInstance_TradeState(OUTSTANDING)
func MarketLayer2DeterminesRequiredTransactionFunctionNameCancelTradeInstanceBasedOnExistingTradeInstanceDerivedTradeTypeGameMechanicsAndTradeInstanceTradeStateOutstanding(input *traderulesv1.GameTradeDomainInput) (*tradeLifecycleDecisionDraft, error) {
	interaction, err := interactionFromDomainInput(input)
	if err != nil {
		return nil, err
	}
	if err := validateExistingTradeInteraction(interaction, "cancelled visible trade"); err != nil {
		return nil, err
	}

	return newTradeLifecycleDecisionDraft(transactionFunctionCancelTradeInstance, interaction), nil
}

// @Market_Layer_2_writes_required_transaction_function_name(...)_and_required_row_identities_into_request_metadata
func MarketLayer2WritesRequiredTransactionFunctionNameAndRequiredRowIdentitiesIntoRequestMetadata(draft *tradeLifecycleDecisionDraft) (*traderulesv1.TradeLifecycleDecision, error) {
	if draft == nil || draft.interaction == nil {
		return nil, ErrMissingInteraction
	}

	metadata, err := buildTransactionMetadata(draft)
	if err != nil {
		return nil, err
	}

	return &traderulesv1.TradeLifecycleDecision{
		SourceInteractionId:         draft.interaction.GetInteractionId(),
		CorrelationId:               draft.interaction.GetCorrelationId(),
		RequiredTransactionMetadata: metadata,
	}, nil
}

func (s *Service) marketLayer3SendsTradeInstanceTransactionRequestToTradeSettlementViaGRPCUsingTheSameTransactionFunctionNameChosenByLayer2(ctx context.Context, decision *traderulesv1.TradeLifecycleDecision) (*evetradev1.TradeInstanceTransactionOutcome, error) {
	if decision == nil || decision.GetRequiredTransactionMetadata() == nil {
		return nil, invalid(ErrMissingTransactionMetadata)
	}

	switch {
	case decision.GetRequiredTransactionMetadata().GetIssueTradeInstance() != nil:
		return s.MarketLayer3SendsIssueTradeInstanceRequestToTradeSettlementViaGRPCUsingTheSameTransactionFunctionNameChosenByLayer2(ctx, decision)
	case decision.GetRequiredTransactionMetadata().GetSettleTradeInstance() != nil:
		return s.MarketLayer3SendsSettleTradeInstanceRequestToTradeSettlementViaGRPCUsingTheSameTransactionFunctionNameChosenByLayer2(ctx, decision)
	case decision.GetRequiredTransactionMetadata().GetCancelTradeInstance() != nil:
		return s.MarketLayer3SendsCancelTradeInstanceRequestToTradeSettlementViaGRPCUsingTheSameTransactionFunctionNameChosenByLayer2(ctx, decision)
	default:
		return nil, invalid(ErrMissingTransactionMetadata)
	}
}

// @Market_Layer_3_sends_issue_trade_instance_request_to_trade_settlement_via_gRPC_using_the_same_transaction_function_name_chosen_by_Layer_2
func (s *Service) MarketLayer3SendsIssueTradeInstanceRequestToTradeSettlementViaGRPCUsingTheSameTransactionFunctionNameChosenByLayer2(ctx context.Context, decision *traderulesv1.TradeLifecycleDecision) (*evetradev1.TradeInstanceTransactionOutcome, error) {
	return s.sendTradeInstanceTransactionRequestToSettlement(ctx, decision)
}

// @Market_Layer_3_sends_settle_trade_instance_request_to_trade_settlement_via_gRPC_using_the_same_transaction_function_name_chosen_by_Layer_2
func (s *Service) MarketLayer3SendsSettleTradeInstanceRequestToTradeSettlementViaGRPCUsingTheSameTransactionFunctionNameChosenByLayer2(ctx context.Context, decision *traderulesv1.TradeLifecycleDecision) (*evetradev1.TradeInstanceTransactionOutcome, error) {
	return s.sendTradeInstanceTransactionRequestToSettlement(ctx, decision)
}

// @Market_Layer_3_sends_cancel_trade_instance_request_to_trade_settlement_via_gRPC_using_the_same_transaction_function_name_chosen_by_Layer_2
func (s *Service) MarketLayer3SendsCancelTradeInstanceRequestToTradeSettlementViaGRPCUsingTheSameTransactionFunctionNameChosenByLayer2(ctx context.Context, decision *traderulesv1.TradeLifecycleDecision) (*evetradev1.TradeInstanceTransactionOutcome, error) {
	return s.sendTradeInstanceTransactionRequestToSettlement(ctx, decision)
}

func (s *Service) sendTradeInstanceTransactionRequestToSettlement(ctx context.Context, decision *traderulesv1.TradeLifecycleDecision) (*evetradev1.TradeInstanceTransactionOutcome, error) {
	if decision == nil || decision.GetRequiredTransactionMetadata() == nil {
		return nil, invalid(ErrMissingTransactionMetadata)
	}

	response, err := s.settlement.SendTradeInstanceTransaction(ctx, &settlementv1.TradeInstanceTransactionRequest{
		Metadata: decision.GetRequiredTransactionMetadata(),
	})
	if err != nil {
		return nil, err
	}
	if response.GetOutcome() == nil {
		return nil, connect.NewError(connect.CodeInternal, ErrMissingSettlementOutcome)
	}

	return response.GetOutcome(), nil
}

func invalid(err error) error {
	return connect.NewError(connect.CodeInvalidArgument, err)
}
