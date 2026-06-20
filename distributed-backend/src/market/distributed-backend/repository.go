package distributedbackend

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type ItemStackSnapshot struct {
	ItemStackID string
	OwnerID     int64
	ItemTypeID  int64
	StationID   int64
	Quantity    int64
	StackState  string
}

type WalletSnapshot struct {
	WalletID    string
	CapsuleerID int64
	ISKAmount   int64
	WalletState string
}

type TradeSnapshot struct {
	TradeInstanceID    string
	TradeState         string
	IssuerID           int64
	ItemTypeID         int64
	StationID          int64
	RemainingQuantity  int64
	UnitPriceISK       int64
	ItemStackEscrowID  string
	EscrowQuantity     int64
	EscrowReleased     bool
	SourceItemStackID  string
	SourceItemOwnerID  int64
	SourceItemStackQty int64
	TotalQuantity      int64
}

type TradeRepository interface {
	LoadItemStack(ctx context.Context, itemStackID string) (ItemStackSnapshot, error)
	LoadWallet(ctx context.Context, walletID string) (WalletSnapshot, error)
	LoadPrimaryWallet(ctx context.Context, capsuleerID int64) (WalletSnapshot, error)
	LoadTrade(ctx context.Context, tradeInstanceID string) (TradeSnapshot, error)
	LoadCompletedIdempotencyReplay(ctx context.Context, idempotencyKey string) (*IdempotencyReplay, error)
}

type IdempotencyReplay struct {
	SettlementBatchID   string
	CausedByCapsuleerID int64
	Steps               []ReplayStep
}

type ReplayStep struct {
	StepKind string
	Payload  map[string]AnyJSON
}

type AnyJSON = any

type PostgresTradeRepository struct {
	pool *pgxpool.Pool
}

func NewPostgresTradeRepository(ctx context.Context, databaseURL string) (*PostgresTradeRepository, error) {
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, fmt.Errorf("create market postgres pool: %w", err)
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, fmt.Errorf("ping market postgres pool: %w", err)
	}
	return &PostgresTradeRepository{pool: pool}, nil
}

func (r *PostgresTradeRepository) Close() {
	r.pool.Close()
}

func (r *PostgresTradeRepository) LoadItemStack(ctx context.Context, itemStackID string) (ItemStackSnapshot, error) {
	var row ItemStackSnapshot
	err := r.pool.QueryRow(ctx, `
		SELECT item_stack_id::text,
		       owner_id,
		       item_type_id,
		       station_id,
		       quantity,
		       stack_state
		FROM item_stack
		WHERE item_stack_id = $1::uuid
	`, itemStackID).Scan(
		&row.ItemStackID,
		&row.OwnerID,
		&row.ItemTypeID,
		&row.StationID,
		&row.Quantity,
		&row.StackState,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return ItemStackSnapshot{}, fmt.Errorf("item_stack %s does not exist", itemStackID)
	}
	if err != nil {
		return ItemStackSnapshot{}, fmt.Errorf("load item_stack %s: %w", itemStackID, err)
	}
	return row, nil
}

func (r *PostgresTradeRepository) LoadWallet(ctx context.Context, walletID string) (WalletSnapshot, error) {
	var row WalletSnapshot
	err := r.pool.QueryRow(ctx, `
		SELECT wallet_id::text,
		       capsuleer_id,
		       isk_amount,
		       wallet_state
		FROM wallet
		WHERE wallet_id = $1::uuid
	`, walletID).Scan(
		&row.WalletID,
		&row.CapsuleerID,
		&row.ISKAmount,
		&row.WalletState,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return WalletSnapshot{}, fmt.Errorf("wallet %s does not exist", walletID)
	}
	if err != nil {
		return WalletSnapshot{}, fmt.Errorf("load wallet %s: %w", walletID, err)
	}
	return row, nil
}

func (r *PostgresTradeRepository) LoadPrimaryWallet(ctx context.Context, capsuleerID int64) (WalletSnapshot, error) {
	var row WalletSnapshot
	err := r.pool.QueryRow(ctx, `
		SELECT wallet_id::text,
		       capsuleer_id,
		       isk_amount,
		       wallet_state
		FROM wallet
		WHERE capsuleer_id = $1
		  AND wallet_kind = 'PRIMARY'
		ORDER BY created_at
		LIMIT 1
	`, capsuleerID).Scan(
		&row.WalletID,
		&row.CapsuleerID,
		&row.ISKAmount,
		&row.WalletState,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return WalletSnapshot{}, fmt.Errorf("primary wallet for capsuleer %d does not exist", capsuleerID)
	}
	if err != nil {
		return WalletSnapshot{}, fmt.Errorf("load primary wallet for capsuleer %d: %w", capsuleerID, err)
	}
	return row, nil
}

func (r *PostgresTradeRepository) LoadTrade(ctx context.Context, tradeInstanceID string) (TradeSnapshot, error) {
	var row TradeSnapshot
	err := r.pool.QueryRow(ctx, `
		SELECT t.trade_instance_id::text,
		       t.trade_state,
		       t.issuer_id,
		       t.item_type_id,
		       t.station_id,
		       t.total_quantity,
		       t.remaining_quantity,
		       t.unit_price_isk,
		       e.item_stack_escrow_id::text,
		       e.quantity,
		       e.is_released,
		       e.source_item_stack_id::text,
		       s.owner_id,
		       s.quantity
		FROM trade_instance t
		JOIN item_stack_escrow e ON e.trade_instance_id = t.trade_instance_id
		JOIN item_stack s ON s.item_stack_id = e.source_item_stack_id
		WHERE t.trade_instance_id = $1::uuid
		ORDER BY e.created_at
		LIMIT 1
	`, tradeInstanceID).Scan(
		&row.TradeInstanceID,
		&row.TradeState,
		&row.IssuerID,
		&row.ItemTypeID,
		&row.StationID,
		&row.TotalQuantity,
		&row.RemainingQuantity,
		&row.UnitPriceISK,
		&row.ItemStackEscrowID,
		&row.EscrowQuantity,
		&row.EscrowReleased,
		&row.SourceItemStackID,
		&row.SourceItemOwnerID,
		&row.SourceItemStackQty,
	)
	if errors.Is(err, pgx.ErrNoRows) {
		return TradeSnapshot{}, fmt.Errorf("trade_instance %s does not exist", tradeInstanceID)
	}
	if err != nil {
		return TradeSnapshot{}, fmt.Errorf("load trade_instance %s: %w", tradeInstanceID, err)
	}
	return row, nil
}

func (r *PostgresTradeRepository) LoadCompletedIdempotencyReplay(ctx context.Context, idempotencyKey string) (*IdempotencyReplay, error) {
	var replay IdempotencyReplay
	err := r.pool.QueryRow(ctx, `
		SELECT ir.result_settlement_batch_id::text,
		       COALESCE(sb.caused_by_capsuleer_id, 0)
		FROM idempotency_record ir
		JOIN settlement_batch sb ON sb.settlement_batch_id = ir.result_settlement_batch_id
		WHERE ir.idempotency_key = $1
		  AND ir.idempotency_state = 'COMPLETED'
	`, idempotencyKey).Scan(&replay.SettlementBatchID, &replay.CausedByCapsuleerID)
	if errors.Is(err, pgx.ErrNoRows) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("load idempotency replay %s: %w", idempotencyKey, err)
	}

	rows, err := r.pool.Query(ctx, `
		SELECT step_kind, step_payload
		FROM settlement_step
		WHERE settlement_batch_id = $1::uuid
		ORDER BY step_index
	`, replay.SettlementBatchID)
	if err != nil {
		return nil, fmt.Errorf("load idempotency replay steps %s: %w", idempotencyKey, err)
	}
	defer rows.Close()

	for rows.Next() {
		var step ReplayStep
		var payloadBytes []byte
		if err := rows.Scan(&step.StepKind, &payloadBytes); err != nil {
			return nil, fmt.Errorf("scan idempotency replay step %s: %w", idempotencyKey, err)
		}
		if err := json.Unmarshal(payloadBytes, &step.Payload); err != nil {
			return nil, fmt.Errorf("decode idempotency replay step %s: %w", idempotencyKey, err)
		}
		replay.Steps = append(replay.Steps, step)
	}
	if err := rows.Err(); err != nil {
		return nil, fmt.Errorf("iterate idempotency replay steps %s: %w", idempotencyKey, err)
	}
	return &replay, nil
}
