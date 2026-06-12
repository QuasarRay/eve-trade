//go:build e2e

// This block declares the e2e test package.
// It exists outside the market package so the tests exercise public service boundaries instead of private functions.
package e2e

import (
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"os"
	"testing"
	"time"

	"connectrpc.com/connect"
	"github.com/jackc/pgx/v5/pgxpool"
	marketv1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/market/v1"
	"github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/market/v1/marketv1connect"
	tradev1 "github.com/QuasarRay/eve-trade/distributed-backend/proto/gen/trade/v1"
	"google.golang.org/protobuf/types/known/timestamppb"
)

// This block defines stable fixture IDs used across every e2e test.
// It exists so database seed rows, request payloads, and assertions refer to the same deterministic world objects.
const (
	seedRegionID      = "11111111-1111-1111-1111-111111111111"
	seedStationID     = "22222222-2222-2222-2222-222222222222"
	seedItemTypeID    = "33333333-3333-3333-3333-333333333333"
	seedSellerID      = "44444444-4444-4444-4444-444444444444"
	seedBuyerID       = "55555555-5555-5555-5555-555555555555"
	seedSellerWallet  = "66666666-6666-6666-6666-666666666666"
	seedBuyerWallet   = "77777777-7777-7777-7777-777777777777"
	seedSellerStack   = "88888888-8888-8888-8888-888888888888"
	seedBuyerStack    = "99999999-9999-9999-9999-999999999999"
	seedSourceSystem  = "e2e-test-game-server"
	seedCreatedBy     = "e2e-market-test"
	initialSellerIsk  = int64(1_000)
	initialBuyerIsk   = int64(100_000)
	initialSellerQty  = int64(100)
	initialBuyerQty   = int64(0)
	orderQty          = uint64(30)
	unitPriceMinorIsk = int64(5)
)

// This block groups the  service client and  database pool used by a test.
// It exists so setup/teardown code stays explicit instead of hiding the integration boundary in globals.
type harness struct {
	market marketv1connect.MarketServiceClient
	db     *pgxpool.Pool
}

// This block describes the mutable fixture values that vary between tests.
// It exists so insufficient-funds and normal-flow tests can share the same seed logic safely.
type seedOptions struct {
	buyerAvailableIsk int64
	sellerQuantity    int64
}

// This block verifies that a sell order can be created through market, settled by the  settlement service, and persisted in PostgreSQL.
// It exists because this is the core portfolio claim: atomic movement of ISK and stackable items through  services.
func TestSellOrderFillMovesItemsAndIskThroughServices(t *testing.T) {
	h := newHarness(t)
	resetAndSeed(t, h.db, seedOptions{buyerAvailableIsk: initialBuyerIsk, sellerQuantity: initialSellerQty})
	waitForMarket(t, h.market)

	order := createSellOrder(t, h.market, "create-sell-happy", orderQty, unitPriceMinorIsk)
	fill := acceptFill(t, h.market, order.GetTradeOrderId().GetValue(), "fill-sell-happy", orderQty)

	if fill.GetTradeOrder().GetState() != tradev1.TransactionState_TRANSACTION_STATE_COMPLETED {
		t.Fatalf("expected completed order after full fill, got %s", fill.GetTradeOrder().GetState().String())
	}

	assertWallet(t, h.db, seedSellerWallet, initialSellerIsk+int64(orderQty)*unitPriceMinorIsk, 0)
	assertWallet(t, h.db, seedBuyerWallet, initialBuyerIsk-int64(orderQty)*unitPriceMinorIsk, 0)
	assertStack(t, h.db, seedSellerStack, initialSellerQty-int64(orderQty), 0)
	assertStack(t, h.db, seedBuyerStack, int64(orderQty), 0)
}

// This block verifies that replaying the same fill command does not move money or items twice.
// It exists because retry-safety is mandatory for distributed systems where clients may repeat a request after a timeout.
func TestReplaySameAcceptFillDoesNotDoubleMoveAssets(t *testing.T) {
	h := newHarness(t)
	resetAndSeed(t, h.db, seedOptions{buyerAvailableIsk: initialBuyerIsk, sellerQuantity: initialSellerQty})
	waitForMarket(t, h.market)

	order := createSellOrder(t, h.market, "create-sell-replay", orderQty, unitPriceMinorIsk)
	first := acceptFill(t, h.market, order.GetTradeOrderId().GetValue(), "fill-replay", orderQty)
	second := acceptFill(t, h.market, order.GetTradeOrderId().GetValue(), "fill-replay", orderQty)

	if first.GetIdempotentReplay() {
		t.Fatal("first fill was incorrectly reported as an idempotent replay")
	}
	if !second.GetIdempotentReplay() {
		t.Fatal("second identical fill should be reported as an idempotent replay")
	}

	assertWallet(t, h.db, seedSellerWallet, initialSellerIsk+int64(orderQty)*unitPriceMinorIsk, 0)
	assertWallet(t, h.db, seedBuyerWallet, initialBuyerIsk-int64(orderQty)*unitPriceMinorIsk, 0)
	assertStack(t, h.db, seedSellerStack, initialSellerQty-int64(orderQty), 0)
	assertStack(t, h.db, seedBuyerStack, int64(orderQty), 0)
}

// This block verifies that an idempotency key cannot be reused for a different fill payload.
// It exists because idempotency is a correctness guard, not a permission to overwrite request meaning.
func TestSameIdempotencyKeyDifferentPayloadConflicts(t *testing.T) {
	h := newHarness(t)
	resetAndSeed(t, h.db, seedOptions{buyerAvailableIsk: initialBuyerIsk, sellerQuantity: initialSellerQty})
	waitForMarket(t, h.market)

	order := createSellOrder(t, h.market, "create-sell-conflict", orderQty, unitPriceMinorIsk)
	_ = acceptFillWithContext(t, h.market, order.GetTradeOrderId().GetValue(), "fill-conflict-request-1", "fill-conflict-key", orderQty)

	_, err := h.market.AcceptFillOrder(context.Background(), connect.NewRequest(&marketv1.AcceptFillOrderRequest{
		Context:              requestContext("fill-conflict-request-2", "fill-conflict-key", seedBuyerID),
		TradeOrderId:         tradeOrderID(order.GetTradeOrderId().GetValue()),
		AcceptingCapsuleerId: capsuleerID(seedBuyerID),
		BuyerWalletId:        walletID(seedBuyerWallet),
		ItemKind:             tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE,
		DestinationItemStackId: itemStackID(seedBuyerStack),
		Quantity:             quantity(orderQty - 1),
	}))

	if codeOf(err) != connect.CodeAlreadyExists {
		t.Fatalf("expected already_exists idempotency conflict, got code=%s err=%v", codeOf(err), err)
	}
}

// This block verifies that settlement failure caused by insufficient buyer ISK does not consume the seller reservation.
// It exists because rollback safety is more important than returning a friendly error.
func TestInsufficientBuyerIskDoesNotMoveItemsOrMoney(t *testing.T) {
	h := newHarness(t)
	resetAndSeed(t, h.db, seedOptions{buyerAvailableIsk: 10, sellerQuantity: initialSellerQty})
	waitForMarket(t, h.market)

	order := createSellOrder(t, h.market, "create-sell-low-buyer-isk", orderQty, unitPriceMinorIsk)
	_, err := h.market.AcceptFillOrder(context.Background(), connect.NewRequest(&marketv1.AcceptFillOrderRequest{
		Context:                requestContext("fill-low-buyer-isk", "fill-low-buyer-isk", seedBuyerID),
		TradeOrderId:           tradeOrderID(order.GetTradeOrderId().GetValue()),
		AcceptingCapsuleerId:   capsuleerID(seedBuyerID),
		BuyerWalletId:          walletID(seedBuyerWallet),
		ItemKind:               tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE,
		DestinationItemStackId: itemStackID(seedBuyerStack),
		Quantity:               quantity(orderQty),
	}))

	if codeOf(err) != connect.CodeFailedPrecondition {
		t.Fatalf("expected failed_precondition for insufficient ISK, got code=%s err=%v", codeOf(err), err)
	}

	assertWallet(t, h.db, seedSellerWallet, initialSellerIsk, 0)
	assertWallet(t, h.db, seedBuyerWallet, 10, 0)
	assertStack(t, h.db, seedSellerStack, initialSellerQty-int64(orderQty), int64(orderQty))
	assertStack(t, h.db, seedBuyerStack, 0, 0)
}

// This block verifies that cancellation by the order owner releases the stack reservation.
// It exists because un-released reservations are a silent asset-locking bug.
func TestCancelSellOrderReleasesReservedStack(t *testing.T) {
	h := newHarness(t)
	resetAndSeed(t, h.db, seedOptions{buyerAvailableIsk: initialBuyerIsk, sellerQuantity: initialSellerQty})
	waitForMarket(t, h.market)

	order := createSellOrder(t, h.market, "create-sell-cancel", orderQty, unitPriceMinorIsk)
	response, err := h.market.CancelOrder(context.Background(), connect.NewRequest(&marketv1.CancelOrderRequest{
		Context:               requestContext("cancel-sell", "cancel-sell", seedSellerID),
		TradeOrderId:          tradeOrderID(order.GetTradeOrderId().GetValue()),
		RequestingCapsuleerId: capsuleerID(seedSellerID),
		Reason:                "e2e owner cancellation",
	}))
	if err != nil {
		t.Fatalf("cancel order failed: %v", err)
	}
	if response.Msg.GetTradeOrder().GetState() != tradev1.TransactionState_TRANSACTION_STATE_CANCELLED {
		t.Fatalf("expected cancelled order, got %s", response.Msg.GetTradeOrder().GetState().String())
	}

	assertStack(t, h.db, seedSellerStack, initialSellerQty, 0)
}

// This block verifies that market rejects a fill quantity larger than the durable remaining quantity while using the  settlement read path.
// It exists because market must not translate impossible fills into settlement commands.
func TestRejectFillQuantityGreaterThanRemaining(t *testing.T) {
	h := newHarness(t)
	resetAndSeed(t, h.db, seedOptions{buyerAvailableIsk: initialBuyerIsk, sellerQuantity: initialSellerQty})
	waitForMarket(t, h.market)

	order := createSellOrder(t, h.market, "create-sell-too-large", orderQty, unitPriceMinorIsk)
	_, err := h.market.AcceptFillOrder(context.Background(), connect.NewRequest(&marketv1.AcceptFillOrderRequest{
		Context:                requestContext("fill-too-large", "fill-too-large", seedBuyerID),
		TradeOrderId:           tradeOrderID(order.GetTradeOrderId().GetValue()),
		AcceptingCapsuleerId:   capsuleerID(seedBuyerID),
		BuyerWalletId:          walletID(seedBuyerWallet),
		ItemKind:               tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE,
		DestinationItemStackId: itemStackID(seedBuyerStack),
		Quantity:               quantity(orderQty + 1),
	}))
	if codeOf(err) != connect.CodeInvalidArgument {
		t.Fatalf("expected invalid_argument for excessive fill quantity, got code=%s err=%v", codeOf(err), err)
	}
}

// This block verifies that market rejects a fill whose item kind disagrees with the durable order.
// It exists because a client must not be able to reinterpret a stackable sell order as a singleton transfer.
func TestRejectMismatchedItemKind(t *testing.T) {
	h := newHarness(t)
	resetAndSeed(t, h.db, seedOptions{buyerAvailableIsk: initialBuyerIsk, sellerQuantity: initialSellerQty})
	waitForMarket(t, h.market)

	order := createSellOrder(t, h.market, "create-sell-kind-mismatch", orderQty, unitPriceMinorIsk)
	_, err := h.market.AcceptFillOrder(context.Background(), connect.NewRequest(&marketv1.AcceptFillOrderRequest{
		Context:                requestContext("fill-kind-mismatch", "fill-kind-mismatch", seedBuyerID),
		TradeOrderId:           tradeOrderID(order.GetTradeOrderId().GetValue()),
		AcceptingCapsuleerId:   capsuleerID(seedBuyerID),
		BuyerWalletId:          walletID(seedBuyerWallet),
		ItemKind:               tradev1.TradeItemKind_TRADE_ITEM_KIND_SINGLETON,
		DestinationItemStackId: itemStackID(seedBuyerStack),
		Quantity:               quantity(orderQty),
	}))
	if codeOf(err) != connect.CodeInvalidArgument {
		t.Fatalf("expected invalid_argument for item-kind mismatch, got code=%s err=%v", codeOf(err), err)
	}
}

// This block verifies that a non-owner cannot cancel someone else's order.
// It exists because market owns the player-facing ownership rule before settlement performs the durable close.
func TestRejectCancelByNonOwner(t *testing.T) {
	h := newHarness(t)
	resetAndSeed(t, h.db, seedOptions{buyerAvailableIsk: initialBuyerIsk, sellerQuantity: initialSellerQty})
	waitForMarket(t, h.market)

	order := createSellOrder(t, h.market, "create-sell-non-owner-cancel", orderQty, unitPriceMinorIsk)
	_, err := h.market.CancelOrder(context.Background(), connect.NewRequest(&marketv1.CancelOrderRequest{
		Context:               requestContext("cancel-non-owner", "cancel-non-owner", seedBuyerID),
		TradeOrderId:          tradeOrderID(order.GetTradeOrderId().GetValue()),
		RequestingCapsuleerId: capsuleerID(seedBuyerID),
		Reason:                "malicious cancel attempt",
	}))
	if codeOf(err) != connect.CodePermissionDenied {
		t.Fatalf("expected permission_denied for non-owner cancellation, got code=%s err=%v", codeOf(err), err)
	}
}

// This block constructs the  market client and  PostgreSQL pool for a test.
// It exists so every test starts with explicit network/database dependencies rather than hidden package state.
func newHarness(t *testing.T) harness {
	t.Helper()
	marketURL := getenv("EVE_TRADE_MARKET_URL", "http://localhost:8081")
	databaseURL := getenv("EVE_TRADE_DATABASE_URL", "postgres://postgres:postgres@localhost:5432/eve_trade")
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	pool, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		t.Fatalf("connect postgres: %v", err)
	}
	t.Cleanup(pool.Close)
	return harness{market: marketv1connect.NewMarketServiceClient(nil, marketURL, connect.WithGRPC()), db: pool}
}

// This block waits until the  market service can answer a read request.
// It exists because Docker `service_started` means the process was launched, not that the gRPC-compatible listener is ready.
func waitForMarket(t *testing.T, client marketv1connect.MarketServiceClient) {
	t.Helper()
	deadline := time.Now().Add(45 * time.Second)
	var lastErr error
	for time.Now().Before(deadline) {
		_, lastErr = client.ListOutstandingOrders(context.Background(), connect.NewRequest(&marketv1.ListOutstandingOrdersRequest{Context: requestContext("wait-market", "wait-market", seedSellerID), PageSize: 1}))
		if lastErr == nil {
			return
		}
		time.Sleep(500 * time.Millisecond)
	}
	t.Fatalf("market did not become ready: %v", lastErr)
}

// This block recreates the minimal world projection rows required by settlement.
// It exists so each test gets deterministic capsuleers, wallets, station, item type, and item stacks.
func resetAndSeed(t *testing.T, pool *pgxpool.Pool, opts seedOptions) {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	if opts.sellerQuantity == 0 {
		opts.sellerQuantity = initialSellerQty
	}
	_, err := pool.Exec(ctx, `TRUNCATE TABLE
		trade.domain_event_outbox,
		trade.idempotency_result,
		trade.trade_claim_item_instance,
		trade.trade_claim_item_stack,
		trade.trade_claim_isk,
		trade.trade_claim,
		trade.trade_state_change,
		trade.settlement_step,
		trade.settlement,
		trade.trade_transaction,
		trade.item_instance_reservation,
		trade.item_stack_reservation,
		trade.wallet_reservation,
		trade.trade_order,
		trade.item_instance_ledger,
		trade.item_instance_operation,
		trade.item_instance,
		trade.item_stack_ledger,
		trade.item_stack_operation,
		trade.item_stack,
		trade.wallet_ledger,
		trade.wallet_operation,
		trade.wallet,
		trade.operation,
		trade.request_attempt,
		trade.idempotency_record,
		trade.item_type,
		trade.station,
		trade.region,
		trade.capsuleer
		CASCADE`)
	if err != nil {
		t.Fatalf("truncate trade schema: %v", err)
	}
	insertWorldRows(t, pool, opts)
}

// This block inserts the stable world projection, wallet, and stack rows used by the tests.
// It exists because settlement correctly requires referenced capsuleers, wallets, item types, stations, and stacks to exist before trading.
func insertWorldRows(t *testing.T, pool *pgxpool.Pool, opts seedOptions) {
	t.Helper()
	ctx := context.Background()
	_, err := pool.Exec(ctx, `
		INSERT INTO trade.capsuleer (capsuleer_id, capsuleer_name, source_system, source_version, last_synced_at) VALUES
		($1::uuid, 'Seller Capsuleer', 'e2e', '1', now()),
		($2::uuid, 'Buyer Capsuleer', 'e2e', '1', now());
		INSERT INTO trade.region (region_id, region_name, source_system, source_version, last_synced_at) VALUES
		($3::uuid, 'The Forge Test Region', 'e2e', '1', now());
		INSERT INTO trade.station (station_id, region_id, station_name, source_system, source_version, last_synced_at) VALUES
		($4::uuid, $3::uuid, 'Jita Test Station', 'e2e', '1', now());
		INSERT INTO trade.item_type (item_type_id, item_type_name, is_stackable, is_singleton_capable, category_name, group_name, catalog_version, source_system, last_synced_at) VALUES
		($5::uuid, 'Tritanium Test Type', true, false, 'Material', 'Mineral', '1', 'e2e', now());`,
		seedSellerID, seedBuyerID, seedRegionID, seedStationID, seedItemTypeID)
	if err != nil {
		t.Fatalf("insert world rows: %v", err)
	}
	insertWallet(t, pool, seedSellerWallet, seedSellerID, initialSellerIsk, 0)
	insertWallet(t, pool, seedBuyerWallet, seedBuyerID, opts.buyerAvailableIsk, 0)
	insertStack(t, pool, seedSellerStack, seedSellerID, seedItemTypeID, seedStationID, opts.sellerQuantity, 0)
	insertStack(t, pool, seedBuyerStack, seedBuyerID, seedItemTypeID, seedStationID, initialBuyerQty, 0)
}

// This block inserts one wallet with the checksum format expected by the Rust settlement code.
// It exists because settlement uses checksums to detect stale or out-of-band wallet mutation.
func insertWallet(t *testing.T, pool *pgxpool.Pool, wallet, capsuleer string, available, reserved int64) {
	t.Helper()
	checksum := walletChecksum(wallet, capsuleer, "personal", available, reserved, "active", 1)
	_, err := pool.Exec(context.Background(), `INSERT INTO trade.wallet
		(wallet_id, capsuleer_id, wallet_kind, available_isk, reserved_isk, wallet_state, wallet_version, wallet_checksum, checksum_algorithm)
		VALUES ($1::uuid, $2::uuid, 'personal', $3, $4, 'active', 1, $5, 'sha256-v1')`,
		wallet, capsuleer, available, reserved, checksum)
	if err != nil {
		t.Fatalf("insert wallet %s: %v", wallet, err)
	}
}

// This block inserts one stack with the checksum format expected by the Rust settlement code.
// It exists because settlement uses checksums to detect stale or out-of-band item mutation.
func insertStack(t *testing.T, pool *pgxpool.Pool, stack, capsuleer, itemType, station string, available, reserved int64) {
	t.Helper()
	checksum := stackChecksum(stack, capsuleer, itemType, station, available, reserved, "active", 1)
	_, err := pool.Exec(context.Background(), `INSERT INTO trade.item_stack
		(item_stack_id, capsuleer_id, item_type_id, station_id, available_quantity, reserved_quantity, stack_state, stack_version, stack_checksum, checksum_algorithm)
		VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6, 'active', 1, $7, 'sha256-v1')`,
		stack, capsuleer, itemType, station, available, reserved, checksum)
	if err != nil {
		t.Fatalf("insert stack %s: %v", stack, err)
	}
}

// This block creates a sell order through the  market service.
// It exists so tests do not bypass market's creation validation or settlement's reservation logic.
func createSellOrder(t *testing.T, client marketv1connect.MarketServiceClient, key string, qty uint64, price int64) *tradev1.TradeOrderView {
	t.Helper()
	response, err := client.CreateSellOrder(context.Background(), connect.NewRequest(&marketv1.CreateSellOrderRequest{
		Context:            requestContext(key, key, seedSellerID),
		SellerCapsuleerId:  capsuleerID(seedSellerID),
		SellerWalletId:     walletID(seedSellerWallet),
		ItemTypeId:         itemTypeID(seedItemTypeID),
		ItemKind:           tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE,
		OfferedItemStackId: itemStackID(seedSellerStack),
		StationId:          stationID(seedStationID),
		RegionId:           regionID(seedRegionID),
		Quantity:           quantity(qty),
		UnitPriceIsk:       isk(price),
		ExpiresAt:          timestamppb.New(time.Now().Add(24 * time.Hour)),
	}))
	if err != nil {
		t.Fatalf("create sell order failed: %v", err)
	}
	if response.Msg.GetTradeOrder() == nil {
		t.Fatal("create sell order returned nil trade_order")
	}
	return response.Msg.GetTradeOrder()
}

// This block accepts a fill using the same request ID and idempotency key.
// It exists for tests where the logical command identity should be stable across retries.
func acceptFill(t *testing.T, client marketv1connect.MarketServiceClient, orderID string, key string, qty uint64) *marketv1.AcceptFillOrderResponse {
	t.Helper()
	return acceptFillWithContext(t, client, orderID, key, key, qty)
}

// This block accepts a fill through the  market service with explicit request and idempotency identifiers.
// It exists so idempotency conflict tests can reuse a key while changing request content deliberately.
func acceptFillWithContext(t *testing.T, client marketv1connect.MarketServiceClient, orderID, requestID, idempotencyKey string, qty uint64) *marketv1.AcceptFillOrderResponse {
	t.Helper()
	response, err := client.AcceptFillOrder(context.Background(), connect.NewRequest(&marketv1.AcceptFillOrderRequest{
		Context:                requestContext(requestID, idempotencyKey, seedBuyerID),
		TradeOrderId:           tradeOrderID(orderID),
		AcceptingCapsuleerId:   capsuleerID(seedBuyerID),
		BuyerWalletId:          walletID(seedBuyerWallet),
		ItemKind:               tradev1.TradeItemKind_TRADE_ITEM_KIND_STACKABLE,
		DestinationItemStackId: itemStackID(seedBuyerStack),
		Quantity:               quantity(qty),
	}))
	if err != nil {
		t.Fatalf("accept fill failed: %v", err)
	}
	return response.Msg
}

// This block asserts one wallet's available and reserved ISK in PostgreSQL.
// It exists so tests verify durable effects, not just response messages.
func assertWallet(t *testing.T, pool *pgxpool.Pool, wallet string, wantAvailable, wantReserved int64) {
	t.Helper()
	var gotAvailable, gotReserved int64
	err := pool.QueryRow(context.Background(), `SELECT available_isk, reserved_isk FROM trade.wallet WHERE wallet_id = $1::uuid`, wallet).Scan(&gotAvailable, &gotReserved)
	if err != nil {
		t.Fatalf("query wallet %s: %v", wallet, err)
	}
	if gotAvailable != wantAvailable || gotReserved != wantReserved {
		t.Fatalf("wallet %s got available=%d reserved=%d, want available=%d reserved=%d", wallet, gotAvailable, gotReserved, wantAvailable, wantReserved)
	}
}

// This block asserts one item stack's available and reserved quantity in PostgreSQL.
// It exists so tests verify durable item movement, not just response messages.
func assertStack(t *testing.T, pool *pgxpool.Pool, stack string, wantAvailable, wantReserved int64) {
	t.Helper()
	var gotAvailable, gotReserved int64
	err := pool.QueryRow(context.Background(), `SELECT available_quantity, reserved_quantity FROM trade.item_stack WHERE item_stack_id = $1::uuid`, stack).Scan(&gotAvailable, &gotReserved)
	if err != nil {
		t.Fatalf("query stack %s: %v", stack, err)
	}
	if gotAvailable != wantAvailable || gotReserved != wantReserved {
		t.Fatalf("stack %s got available=%d reserved=%d, want available=%d reserved=%d", stack, gotAvailable, gotReserved, wantAvailable, wantReserved)
	}
}

// This block converts a Go error into a connect error code.
// It exists so tests can assert transport semantics without string-matching error messages.
func codeOf(err error) connect.Code {
	if err == nil {
		return connect.CodeOK
	}
	var connectErr *connect.Error
	if errors.As(err, &connectErr) {
		return connectErr.Code()
	}
	return connect.CodeUnknown
}

// This block builds the shared request context wrapper expected by market and settlement.
// It exists so every command has explicit request ID, idempotency key, source system, creator, and acting capsuleer.
func requestContext(request, key, acting string) *tradev1.RequestContext {
	return &tradev1.RequestContext{
		RequestId:           requestID(uuidFromKey(request)),
		IdempotencyKey:      idempotencyKey(key),
		CompatibilityDate:   "2026-06-12",
		SourceSystem:        seedSourceSystem,
		CreatedByService:    seedCreatedBy,
		ActingCapsuleerId:   capsuleerID(acting),
	}
}

// This block maps stable human-readable test keys into valid UUID strings.
// It exists because the current settlement boundary validates request IDs as UUIDs.
func uuidFromKey(key string) string {
	sum := sha256.Sum256([]byte(key))
	b := sum[:16]
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}

// This block wraps a string as a RequestId protobuf message.
// It exists to keep request construction compact while preserving strongly named proto wrappers.
func requestID(value string) *tradev1.RequestId { return &tradev1.RequestId{Value: value} }

// This block wraps a string as an IdempotencyKey protobuf message.
// It exists to keep request construction compact while preserving strongly named proto wrappers.
func idempotencyKey(value string) *tradev1.IdempotencyKey { return &tradev1.IdempotencyKey{Value: value} }

// This block wraps a string as a CapsuleerId protobuf message.
// It exists to keep request construction compact while preserving strongly named proto wrappers.
func capsuleerID(value string) *tradev1.CapsuleerId { return &tradev1.CapsuleerId{Value: value} }

// This block wraps a string as a WalletId protobuf message.
// It exists to keep request construction compact while preserving strongly named proto wrappers.
func walletID(value string) *tradev1.WalletId { return &tradev1.WalletId{Value: value} }

// This block wraps a string as an ItemTypeId protobuf message.
// It exists to keep request construction compact while preserving strongly named proto wrappers.
func itemTypeID(value string) *tradev1.ItemTypeId { return &tradev1.ItemTypeId{Value: value} }

// This block wraps a string as an ItemStackId protobuf message.
// It exists to keep request construction compact while preserving strongly named proto wrappers.
func itemStackID(value string) *tradev1.ItemStackId { return &tradev1.ItemStackId{Value: value} }

// This block wraps a string as a StationId protobuf message.
// It exists to keep request construction compact while preserving strongly named proto wrappers.
func stationID(value string) *tradev1.StationId { return &tradev1.StationId{Value: value} }

// This block wraps a string as a RegionId protobuf message.
// It exists to keep request construction compact while preserving strongly named proto wrappers.
func regionID(value string) *tradev1.RegionId { return &tradev1.RegionId{Value: value} }

// This block wraps a string as a TradeOrderId protobuf message.
// It exists to keep request construction compact while preserving strongly named proto wrappers.
func tradeOrderID(value string) *tradev1.TradeOrderId { return &tradev1.TradeOrderId{Value: value} }

// This block wraps an integer as a Quantity protobuf message.
// It exists to avoid floating point quantities in trade correctness tests.
func quantity(value uint64) *tradev1.Quantity { return &tradev1.Quantity{Units: value} }

// This block wraps an integer as an IskAmount protobuf message.
// It exists to avoid floating point money in trade correctness tests.
func isk(value int64) *tradev1.IskAmount { return &tradev1.IskAmount{MinorUnits: value} }

// This block hashes a wallet row using the same field order as the Rust settlement checksum module.
// It exists so seeded wallet rows are accepted as structurally current by settlement.
func walletChecksum(walletID, capsuleerID, kind string, available, reserved int64, state string, version int64) string {
	h := sha256.New()
	writeText(h, walletID)
	writeText(h, capsuleerID)
	writeText(h, kind)
	writeInt64(h, available)
	writeInt64(h, reserved)
	writeText(h, state)
	writeInt64(h, version)
	return hex.EncodeToString(h.Sum(nil))
}

// This block hashes an item-stack row using the same field order as the Rust settlement checksum module.
// It exists so seeded item-stack rows are accepted as structurally current by settlement.
func stackChecksum(stackID, capsuleerID, itemTypeID, stationID string, available, reserved int64, state string, version int64) string {
	h := sha256.New()
	writeText(h, stackID)
	writeText(h, capsuleerID)
	writeText(h, itemTypeID)
	writeText(h, stationID)
	writeInt64(h, available)
	writeInt64(h, reserved)
	writeText(h, state)
	writeInt64(h, version)
	return hex.EncodeToString(h.Sum(nil))
}

// This block writes the Rust checksum module's length-prefixed text encoding.
// It exists to avoid ambiguous hash streams such as ["ab", "c"] and ["a", "bc"].
func writeText(h interface{ Write([]byte) (int, error) }, value string) {
	var size [8]byte
	binary.BigEndian.PutUint64(size[:], uint64(len(value)))
	_, _ = h.Write(size[:])
	_, _ = h.Write([]byte(value))
}

// This block writes the Rust checksum module's signed 64-bit integer encoding.
// It exists so Go test checksums match the service's big-endian i64 hashing exactly.
func writeInt64(h interface{ Write([]byte) (int, error) }, value int64) {
	var buf [8]byte
	binary.BigEndian.PutUint64(buf[:], uint64(value))
	_, _ = h.Write(buf[:])
}

// This block reads an environment variable with a fallback.
// It exists so the same tests can run inside compose or directly from the host.
func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}
