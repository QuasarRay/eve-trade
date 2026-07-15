package settlementworker

import (
	"fmt"

	"buf.build/go/protovalidate"
	"github.com/QuasarRay/eve-trade/distributed-backend/internal/settlementrpc"
	"github.com/QuasarRay/eve-trade/distributed-backend/src/settlement"
	tradesettlementv1 "github.com/QuasarRay/eve-trade/proto/gen/eve/trade_settlement/v1"
)

func toProtoRequest(work *settlement.Work) (*tradesettlementv1.ExecuteSettlementBatchRequest, error) {
	if work == nil {
		return nil, fmt.Errorf("settlement work is required")
	}
	operations := make([]*tradesettlementv1.SettlementOperation, 0, len(work.Operations))
	for i := range work.Operations {
		operation, err := toProtoOperation(work.Operations[i])
		if err != nil {
			return nil, fmt.Errorf("operation %d: %w", i, err)
		}
		operations = append(operations, operation)
	}
	request := &tradesettlementv1.ExecuteSettlementBatchRequest{
		Intent:            settlementIntent(work.Intent),
		IdempotencyKey:    work.IdempotencyKey,
		ExternalRequestId: work.ExternalRequestID,
		Operations:        operations,
		CreatedByService:  work.CreatedByService,
		RequestId:         work.RequestID,
	}
	if work.CausedByCapsuleerID != 0 {
		request.CausedByCapsuleerId = &work.CausedByCapsuleerID
	}
	if err := protovalidate.Validate(request); err != nil {
		return nil, err
	}
	return request, nil
}

func settlementIntent(intent string) tradesettlementv1.SettlementIntent {
	switch intent {
	case settlement.IntentIssue:
		return tradesettlementv1.SettlementIntent_SETTLEMENT_INTENT_ISSUE
	case settlement.IntentAccept:
		return tradesettlementv1.SettlementIntent_SETTLEMENT_INTENT_ACCEPT
	case settlement.IntentCancel:
		return tradesettlementv1.SettlementIntent_SETTLEMENT_INTENT_CANCEL
	default:
		return tradesettlementv1.SettlementIntent_SETTLEMENT_INTENT_UNSPECIFIED
	}
}

func toProtoOperation(operation settlement.Operation) (*tradesettlementv1.SettlementOperation, error) {
	switch operation.Kind {
	case settlement.OperationCreateNewTradeInstanceRow:
		value := operation.CreateNewTradeInstanceRow
		if value == nil {
			return nil, fmt.Errorf("create_new_trade_instance_row payload is required")
		}
		protoValue := &tradesettlementv1.CreateNewTradeInstanceRow{
			TradeInstanceId: value.TradeInstanceID,
			TradeKind:       value.TradeKind,
			TradeState:      value.TradeState,
			IssuerId:        value.IssuerID,
			ItemTypeId:      value.ItemTypeID,
			StationId:       value.StationID,
			TotalQuantity:   value.TotalQuantity,
			UnitPriceIsk:    value.UnitPriceISK,
		}
		if value.ExpiresAt != nil {
			protoValue.ExpiresAt = settlementrpc.Timestamp(*value.ExpiresAt)
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_CreateNewTradeInstanceRow{CreateNewTradeInstanceRow: protoValue},
		}, nil
	case settlement.OperationModifyTradeInstanceState:
		value := operation.ModifyTradeInstanceState
		if value == nil {
			return nil, fmt.Errorf("modify_trade_instance_state payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_ModifyTradeInstanceState{
				ModifyTradeInstanceState: &tradesettlementv1.ModifyTradeInstanceState{
					TradeInstanceId:      value.TradeInstanceID,
					ToTradeState:         value.ToTradeState,
					TradeStateChangeKind: value.TradeStateChangeKind,
					ChangedByService:     value.ChangedByService,
				},
			},
		}, nil
	case settlement.OperationCreateNewEmptyItemStack:
		value := operation.CreateNewEmptyItemStack
		if value == nil {
			return nil, fmt.Errorf("create_new_empty_item_stack payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_CreateNewEmptyItemStack{
				CreateNewEmptyItemStack: &tradesettlementv1.CreateNewEmptyItemStack{
					ItemStackId: value.ItemStackID,
					OwnerId:     value.OwnerID,
					ItemTypeId:  value.ItemTypeID,
					StationId:   value.StationID,
				},
			},
		}, nil
	case settlement.OperationTransferQuantityFromItemStackToItemStackEscrow:
		value := operation.TransferQuantityFromItemStackToItemStackEscrow
		if value == nil {
			return nil, fmt.Errorf("transfer_quantity_from_item_stack_to_item_stack_escrow payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferQuantityFromItemStackToItemStackEscrow{
				TransferQuantityFromItemStackToItemStackEscrow: &tradesettlementv1.TransferQuantityFromItemStackToItemStackEscrow{
					SourceItemStackId: value.SourceItemStackID,
					ItemStackEscrowId: value.ItemStackEscrowID,
					TradeInstanceId:   value.TradeInstanceID,
					Quantity:          value.Quantity,
				},
			},
		}, nil
	case settlement.OperationTransferQuantityFromItemStackEscrowToItemStackWithNewOwner:
		value := operation.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner
		if value == nil {
			return nil, fmt.Errorf("transfer_quantity_from_item_stack_escrow_to_item_stack_with_new_owner payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferQuantityFromItemStackEscrowToItemStackWithNewOwner{
				TransferQuantityFromItemStackEscrowToItemStackWithNewOwner: &tradesettlementv1.TransferQuantityFromItemStackEscrowToItemStackWithNewOwner{
					ItemStackEscrowId:      value.ItemStackEscrowID,
					DestinationItemStackId: value.DestinationItemStackID,
					Quantity:               value.Quantity,
				},
			},
		}, nil
	case settlement.OperationTransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner:
		value := operation.TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner
		if value == nil {
			return nil, fmt.Errorf("transfer_quantity_from_item_stack_escrow_to_item_stack_with_previous_owner payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner{
				TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner: &tradesettlementv1.TransferQuantityFromItemStackEscrowToItemStackWithPreviousOwner{
					ItemStackEscrowId:      value.ItemStackEscrowID,
					DestinationItemStackId: value.DestinationItemStackID,
					Quantity:               value.Quantity,
				},
			},
		}, nil
	case settlement.OperationMergeItemStacksWithIdenticalItemTypeAndIdenticalOwner:
		value := operation.MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner
		if value == nil {
			return nil, fmt.Errorf("merge_item_stacks_with_identical_item_type_and_identical_owner payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner{
				MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner: &tradesettlementv1.MergeItemStacksWithIdenticalItemTypeAndIdenticalOwner{
					SourceItemStackId:      value.SourceItemStackID,
					DestinationItemStackId: value.DestinationItemStackID,
				},
			},
		}, nil
	case settlement.OperationCreateNewEmptyWalletEscrow:
		value := operation.CreateNewEmptyWalletEscrow
		if value == nil {
			return nil, fmt.Errorf("create_new_empty_wallet_escrow payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_CreateNewEmptyWalletEscrow{
				CreateNewEmptyWalletEscrow: &tradesettlementv1.CreateNewEmptyWalletEscrow{
					WalletEscrowId:  value.WalletEscrowID,
					TradeInstanceId: value.TradeInstanceID,
					OwnerId:         value.OwnerID,
					SourceWalletId:  value.SourceWalletID,
				},
			},
		}, nil
	case settlement.OperationTransferISKAmountFromWalletToWalletEscrow:
		value := operation.TransferISKAmountFromWalletToWalletEscrow
		if value == nil {
			return nil, fmt.Errorf("transfer_isk_amount_from_wallet_to_wallet_escrow payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferIskAmountFromWalletToWalletEscrow{
				TransferIskAmountFromWalletToWalletEscrow: &tradesettlementv1.TransferIskAmountFromWalletToWalletEscrow{
					SourceWalletId:  value.SourceWalletID,
					WalletEscrowId:  value.WalletEscrowID,
					TradeInstanceId: value.TradeInstanceID,
					IskAmount:       value.ISKAmount,
				},
			},
		}, nil
	case settlement.OperationTransferISKAmountFromWalletEscrowToWalletWithNewOwner:
		value := operation.TransferISKAmountFromWalletEscrowToWalletWithNewOwner
		if value == nil {
			return nil, fmt.Errorf("transfer_isk_amount_from_wallet_escrow_to_wallet_with_new_owner payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferIskAmountFromWalletEscrowToWalletWithNewOwner{
				TransferIskAmountFromWalletEscrowToWalletWithNewOwner: &tradesettlementv1.TransferIskAmountFromWalletEscrowToWalletWithNewOwner{
					WalletEscrowId:      value.WalletEscrowID,
					DestinationWalletId: value.DestinationWalletID,
					IskAmount:           value.ISKAmount,
				},
			},
		}, nil
	case settlement.OperationTransferISKAmountFromWalletEscrowToWalletWithPreviousOwner:
		value := operation.TransferISKAmountFromWalletEscrowToWalletWithPreviousOwner
		if value == nil {
			return nil, fmt.Errorf("transfer_isk_amount_from_wallet_escrow_to_wallet_with_previous_owner payload is required")
		}
		return &tradesettlementv1.SettlementOperation{
			Operation: &tradesettlementv1.SettlementOperation_TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner{
				TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner: &tradesettlementv1.TransferIskAmountFromWalletEscrowToWalletWithPreviousOwner{
					WalletEscrowId:      value.WalletEscrowID,
					DestinationWalletId: value.DestinationWalletID,
					IskAmount:           value.ISKAmount,
				},
			},
		}, nil
	default:
		return nil, fmt.Errorf("unsupported settlement operation kind %q", operation.Kind)
	}
}
