package gateway

import (
	"context"
	"errors"
	"io"

	"connectrpc.com/connect"
	evetradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/eve_trade/v1"
)

type Service struct {
	market Market
}

func NewService(market Market) *Service {
	return &Service{market: market}
}

func (s *Service) StreamGameTradeUiActivities(ctx context.Context, stream *connect.BidiStream[evetradev1.GameTradeUiActivity, evetradev1.GameTradeUiActivityResult]) error {
	for {
		activity, err := stream.Receive()
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}

		result, err := s.APIGatewayReceivesGameUIActivityFromGameServerViaGRPC(ctx, activity)
		if err != nil {
			return err
		}
		if err := stream.Send(result); err != nil {
			return err
		}
	}
}

// @API-gateway receives GameUI activity(...) from Game Server via gRPC.
func (s *Service) APIGatewayReceivesGameUIActivityFromGameServerViaGRPC(ctx context.Context, activity *evetradev1.GameTradeUiActivity) (*evetradev1.GameTradeUiActivityResult, error) {
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
func APIGatewayTranslatesGameUIActivitiesToProjectProtoContract(activity *evetradev1.GameTradeUiActivity) (*evetradev1.ProjectTradeInteraction, error) {
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

	return &evetradev1.ProjectTradeInteraction{
		InteractionId:          &evetradev1.ProjectTradeInteractionId{Value: stableID("project-interaction", activity.GetGameServerId().GetValue(), activity.GetGameSessionId().GetValue(), activity.GetActivityId().GetValue())},
		SourceActivityId:       activity.GetActivityId(),
		CorrelationId:          &evetradev1.CorrelationId{Value: stableID("correlation", activity.GetGameServerId().GetValue(), activity.GetActivityId().GetValue())},
		TraceId:                &evetradev1.TraceId{Value: stableID("trace", activity.GetGameServerId().GetValue(), activity.GetGameSessionId().GetValue(), activity.GetActivityId().GetValue())},
		CapsuleerId:            activity.GetCapsuleerId(),
		GameSessionId:          activity.GetGameSessionId(),
		InteractionKind:        projectInteractionKind(activity.GetActivityKind()),
		TradeWindow:            knownTradeWindow(activity.GetRawGameScreenName(), fields),
		TradeButton:            knownTradeButton(activity.GetActivityKind(), activity.GetRawGameButtonName()),
		VisibleTradeHubId:      fields.tradeHubID(),
		VisibleTradeInstanceId: fields.tradeInstanceID(),
		SelectedItems:          selectedItems,
		TypedValues:            typedValues,
		OccurredAtUnixMillis:   occurredAtUnixMillis(activity),
	}, nil
}

// @API-gateway sends translated Project Proto interactions to Market microservice.
func (s *Service) APIGatewaySendsProjectProtoInteractionsToMarketMicroservice(ctx context.Context, interaction *evetradev1.ProjectTradeInteraction) (*MarketInteractionResult, error) {
	return s.market.SendProjectTradeInteraction(ctx, interaction)
}

func gameTradeUIActivityResultFromMarketResult(activity *evetradev1.GameTradeUiActivity, marketResult *MarketInteractionResult) *evetradev1.GameTradeUiActivityResult {
	return &evetradev1.GameTradeUiActivityResult{
		ActivityId:               activity.GetActivityId(),
		CorrelationId:            marketResult.GetCorrelationId(),
		PlayerSafeStatus:         playerSafeStatus(marketResult),
		PlayerSafeMessage:        playerSafeMessage(marketResult),
		PlayerSafeTradeReference: playerSafeTradeReference(marketResult),
	}
}
