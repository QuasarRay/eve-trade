package gateway

import (
	"context"
	"errors"
	"io"

	"connectrpc.com/connect"
	commonv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/common/v1"
	gatewayv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/gateway/v1"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/market/v1"
)

type Service struct {
	market Market
}

func NewService(market Market) *Service {
	return &Service{market: market}
}

func (s *Service) StreamGameTradeUiActivities(ctx context.Context, stream *connect.BidiStream[gatewayv1.StreamGameTradeUiActivitiesRequest, gatewayv1.StreamGameTradeUiActivitiesResponse]) error {
	for {
		request, err := stream.Receive()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}

		result, err := s.APIGatewayReceivesGameUIActivityFromGameServerViaGRPC(ctx, request.GetActivity())
		if err != nil {
			return err
		}
		if err := stream.Send(&gatewayv1.StreamGameTradeUiActivitiesResponse{Result: result}); err != nil {
			return err
		}
	}
}

// @API-gateway receives GameUI activity(...) from Game Server via gRPC.
func (s *Service) APIGatewayReceivesGameUIActivityFromGameServerViaGRPC(ctx context.Context, activity *gatewayv1.GameTradeUiActivity) (*gatewayv1.GameTradeUiActivityResult, error) {
	interaction, err := APIGatewayTranslatesGameUIActivitiesToProjectProtoContract(activity)
	if err != nil {
		return nil, connect.NewError(connect.CodeInvalidArgument, err)
	}

	marketResult, err := s.APIGatewaySendsProjectProtoInteractionsToMarketMicroservice(ctx, interaction)
	if err != nil {
		return nil, err
	}

	return gameTradeUIActivityResultFromMarketResult(activity, marketResult), nil
}

// @API-gateway translates GameUI activities to Project Proto contract.
func APIGatewayTranslatesGameUIActivitiesToProjectProtoContract(activity *gatewayv1.GameTradeUiActivity) (*marketv1.ProjectTradeInteraction, error) {
	if err := validateGameTradeUIActivity(activity); err != nil {
		return nil, err
	}

	fields := newVisibleFieldSet(activity.GetVisibleFields())
	typedValues, err := fields.typedValues()
	if err != nil {
		return nil, err
	}
	selectedItems, err := fields.selectedItems(typedValues)
	if err != nil {
		return nil, err
	}
	visibleContext, err := fields.visibleTradeContext()
	if err != nil {
		return nil, err
	}

	return &marketv1.ProjectTradeInteraction{
		InteractionId:        &commonv1.ProjectTradeInteractionId{Value: stableID("project-interaction", activity.GetGameServerId().GetValue(), activity.GetGameSessionId().GetValue(), activity.GetActivityId().GetValue())},
		SourceActivityId:     activity.GetActivityId(),
		CorrelationId:        &commonv1.CorrelationId{Value: stableID("correlation", activity.GetGameServerId().GetValue(), activity.GetActivityId().GetValue())},
		TraceId:              &commonv1.TraceId{Value: stableID("trace", activity.GetGameServerId().GetValue(), activity.GetGameSessionId().GetValue(), activity.GetActivityId().GetValue())},
		CapsuleerId:          activity.GetCapsuleerId(),
		GameSessionId:        activity.GetGameSessionId(),
		InteractionKind:      projectInteractionKind(activity.GetActivityKind()),
		TradeWindow:          knownTradeWindow(activity.GetRawGameScreenName(), fields),
		TradeButton:          knownTradeButton(activity.GetActivityKind(), activity.GetRawGameButtonName()),
		VisibleTradeContext:  visibleContext,
		SelectedItems:        selectedItems,
		TypedValues:          typedValues,
		OccurredAtUnixMillis: occurredAtUnixMillis(activity),
	}, nil
}

// @API-gateway sends translated Project Proto interactions to Market microservice.
func (s *Service) APIGatewaySendsProjectProtoInteractionsToMarketMicroservice(ctx context.Context, interaction *marketv1.ProjectTradeInteraction) (*MarketInteractionResult, error) {
	return s.market.SendProjectTradeInteraction(ctx, interaction)
}

func gameTradeUIActivityResultFromMarketResult(activity *gatewayv1.GameTradeUiActivity, marketResult *MarketInteractionResult) *gatewayv1.GameTradeUiActivityResult {
	return &gatewayv1.GameTradeUiActivityResult{
		ActivityId:               activity.GetActivityId(),
		CorrelationId:            marketResult.GetCorrelationId(),
		ResultStatus:             playerSafeGatewayStatus(marketResult),
		PlayerSafeMessage:        playerSafeMessage(marketResult),
		PlayerSafeTradeReference: playerSafeTradeReference(marketResult),
	}
}
