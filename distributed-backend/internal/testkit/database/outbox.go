package database

import (
	"context"
	"encoding/json"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

func SeedOutboxCapsuleer(ctx context.Context, pool *pgxpool.Pool) error {
	_, err := pool.Exec(ctx, `INSERT INTO capsuleer (capsuleer_id, capsuleer_name) VALUES (1001, 'Outbox Tester')`)
	return err
}

func InsertQueuedOperation(ctx context.Context, tx pgx.Tx, operationID string) error {
	_, err := tx.Exec(ctx, `
		INSERT INTO settlement_operation (
			operation_id, idempotency_key, request_fingerprint, intent,
			caused_by_capsuleer_id, operation_state
		) VALUES ($1::uuid, $2, $3, 'ISSUE', 1001, 'QUEUED')
	`, operationID, "idempotency-"+operationID, "fingerprint-"+operationID)
	return err
}

func InsertOutbox(ctx context.Context, tx pgx.Tx, operationID string, payload any) error {
	encoded, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `
		INSERT INTO settlement_outbox (
			operation_id, message_key, payload, delivery_state, attempt_count, created_at
		) VALUES ($1::uuid, $1, $2::jsonb, 'PENDING', 0, now())
	`, operationID, encoded)
	return err
}

func MarkOutboxDelivered(ctx context.Context, pool *pgxpool.Pool, operationID string) error {
	_, err := pool.Exec(ctx, `UPDATE settlement_outbox SET delivery_state='DELIVERED' WHERE operation_id=$1`, operationID)
	return err
}
