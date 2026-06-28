# GUI simulator first-draft QA coverage report

Run ID: `20260627122639`  
Result: **53 passed checks, 4 detected GUI contract gaps**  
Video: `video/gui-simulator-qa.webm` (1440×900, 25 fps, 2:47)  
Screenshots: 19  
Final invariants: 4,000,000 total ISK, 180 total active item quantity, zero open trades.

## Scope and evidence model

The browser interacted only with the simulator UI. The recorder then queried PostgreSQL to prove settlement effects that the current GUI does not render. Consequently, trade state, wallet changes, inventory changes, escrow consistency, and duplicate-settlement claims are database-backed QA evidence, not visible client views. The video makes this distinction in its captions.

The recorded production-shaped path was:

`browser -> Django simulator -> signed UDP -> Quilkin -> API gateway -> Market -> RabbitMQ -> settlement-worker -> Rust trade-settlement -> PostgreSQL`

## GUI features discovered

| Surface | Visible features | Result |
|---|---|---|
| Header | Simulator title and `quilkin:26001` endpoint label | Visible; endpoint is an internal container address, not a useful player-facing status |
| Neocom | Inventory, Market, Contracts, Wallet tiles | Visible but decorative `div` elements; not clickable or keyboard-focusable |
| Regional Market | Item stack, trade ID, buyer wallet, quantity, price, destination stack fields | Inputs work; UUIDs must be entered manually |
| Market actions | Sell This Item, Buy, Cancel Order | All exercised successfully and negatively |
| Contract actions | Create Item Exchange, Accept Item Exchange | Both exercised; aliases of the same settlement lifecycle |
| Direct trade actions | Offer, Accept | Both exercised; aliases of the same settlement lifecycle |
| Identity/context | Seller ID, buyer ID, station ID, item type ID | Raw numeric inputs only; no authenticated player/station context |
| Packet controls | Idempotency key, external request ID, extra JSON payload | Idempotency works; extra JSON overrides work; external request ID is stripped before UDP |
| Feedback | Gateway Response panel | Readable empty state and raw JSON/error output; no structured success/error presentation |
| Tooltips | Tooltip on every seeded action | 7/7 populated |
| Responsive/state navigation | One static page | Refresh loads cleanly but loses form/trade context; no routes, tabs, or stateful views |

## Scenarios successfully exercised

### Startup and basic GUI

- Simulator loaded without browser-page failure.
- Seven seeded action buttons and all tooltips were present.
- Initial `No interaction sent` state was readable.
- Full browser reload returned a working clean page.
- Malformed extra JSON was rejected client-side without a trade mutation.

### Happy, partial, cancellation, and aliases

- Issued a four-unit market order; PostgreSQL showed `OPEN`, remaining `4`.
- Fully accepted it; PostgreSQL showed `COMPLETED`, remaining `0`.
- Verified buyer items `+4`, seller ISK `+100`, buyer ISK `-100`.
- Issued ten units, accepted four, verified six remained and state stayed `OPEN`.
- Accepted the remaining six and verified the same trade completed without a phantom row.
- Cancelled a five-unit open order and verified all five items returned.
- Accepted three of eight, cancelled the remainder, verified buyer retained three and seller received only five back.
- Created/accepted an item-exchange contract.
- Offered/accepted a direct trade.

### Invalid issue/create

- Zero, negative, blank, excessive, non-numeric, and beyond-`int64` quantity.
- Zero, negative, blank, and beyond-`int64` price.
- Missing seller and item-stack IDs.
- Wrong owner and wrong station projections.
- Confirmed rejection and no trade-row mutation for each case.

### Invalid accept and cancellation

- Zero, negative, excessive, non-numeric, and beyond-`int64` quantity.
- Missing wallet and missing trade ID.
- Wallet owned by another capsuleer.
- Insufficient ISK.
- Seller attempting to buy their own order.
- Accept cancelled and completed trades.
- Cancel completed and already-cancelled trades.
- Cancel as a non-seller; backend denied it and the trade remained open until owner cleanup.

### Retry, duplicate, race, and resilience

- Double-click Issue: one trade and one settlement batch.
- Double-click Accept: one item transfer and one debit/credit.
- Double-click Cancel: one refund.
- Immediate reload after Issue: at most one trade and one settlement; cleanup restored state.
- Two tabs accepted the same two-unit trade with distinct IDs: one response accepted, one rejected, and settlement occurred once.
- Stopped only Market: GUI showed `failed: timed out`.
- Restarted Market: subsequent GUI issue succeeded and was safely cleaned up.
- Final ISK and item conservation checks passed; no trade remained open.

## Not testable through the current GUI

- Seeded player names, player selection, authentication, or a genuine seller/buyer/third-party view.
- Market listing/browse view, buyer discovery of outstanding orders, selected-trade state, or state badges.
- Visible inventory, item ownership, escrow, wallet balance, refund, or trade-history views.
- Visible remaining quantity after a partial fill or visible completed/cancelled state after actions.
- Proving in the UI that completed/cancelled trades are no longer buyable; this was proven only through rejected raw-ID actions and database state.
- Role-aware button enablement/visibility; every action remains visible for every typed capsuleer ID.
- Buyer wrong-station acceptance. The accept action derives station from the trade and ignores the visible station field.
- Contract/direct-trade cancellation because no corresponding seeded buttons exist.
- Trade expiration because no expiration control exists.
- Seller cancellation while a buyer watches a live trade; there is no live trade view or update mechanism.
- Browser Back resubmission; the simulator has one non-navigating page and no form route/history entry.
- Listing empty states, pagination, sorting, filtering, selection, or stale-view recovery; none exist.

## Issues found

### Fixed in this draft

1. **Quilkin never started from local Compose.** String-form command arguments were split so the container executed only `set` and exited `0`. `compose.yaml` now uses a single shell-script argument, matching the working integration Compose pattern.
2. **Fresh PostgreSQL volume could race migration.** `pg_isready` became successful before `eve_trade` was connectable. The migration now retries an actual `SELECT 1` before applying schema and seed files.
3. **Every seeded GUI issue was rejected.** The browser hard-coded item-stack quantity `10` while the seed contains `100`. The simulator now sends `0`, telling Market to use its canonical PostgreSQL projection.

### Open GUI/UX issues

1. **No player-facing state views (high).** The client cannot show listings, selected trades, status, remaining quantity, inventory, wallets, refunds, or history.
2. **No role-aware UI (high).** All seven actions remain visible regardless of seller/buyer identity. Authorization exists only in the backend.
3. **Actions remain enabled in flight (high).** The outage checkpoint proved the clicked action stays enabled, allowing duplicate submission attempts.
4. **External Request ID is misleading (medium).** The field is visible but deliberately stripped from the outgoing GUI packet.
5. **Neocom looks interactive but is not (medium).** Tiles are unfocusable `div` elements without actions.
6. **Raw internal feedback (medium).** Success is a large serializer dump; errors may expose SQLSTATE and internal model/packet details. The missing-wallet case visibly returned PostgreSQL UUID syntax details.
7. **No explicit success/error/loading styling (medium).** `aria-live` announces the response, but there are no durable success/error banners, spinners, or per-action pending states.
8. **Nested outcome semantics are confusing (medium).** A simulator request can be recorded as `sent` while its nested gateway payload is an error; the user must interpret JSON.
9. **Refresh discards context (medium).** IDs and action results are not restored from canonical state.
10. **Endpoint label is implementation-facing (low).** `quilkin:26001` is meaningful inside Docker, not to a player.

## Observability result

- The final recording-window log scan found no panic/fatal SQL, RabbitMQ, worker, Market, gateway, Quilkin, or Rust settlement signature.
- The browser console contains one expected HTTP `502` resource line from the intentional outage.
- The earlier fresh-volume startup race produced a PostgreSQL `FATAL database does not exist` before the final run; it was diagnosed and fixed with SQL readiness retry and is excluded from the final recording-window log scan.

## Recommended work before a final portfolio recording

1. Add read-only player context, inventory, wallet, open/completed/cancelled trade views backed by canonical APIs.
2. Replace raw IDs with selectable seeded players/items/trades and make controls role-aware.
3. Add per-action pending/disabled state, stable success/error banners, and normalized safe client messages.
4. Remove or implement the External Request ID field and make Neocom tiles semantic navigation controls.
5. Add live refresh/polling for trade state and wallet/inventory projections, including readable empty states.
6. Keep this Playwright recorder as the regression pass, then create a shorter human portfolio cut from its strongest checkpoints.

## Commands executed

| Command | Result |
|---|---|
| `pnpm install --no-frozen-lockfile` | Installed pinned Playwright 1.55.0; lockfile generated |
| `playwright install chromium` | Chromium, headless shell, and Playwright FFmpeg installed |
| `docker compose up -d --build simulator` | Images built; exposed and fixed the local Quilkin command defect |
| `docker compose exec -T simulator python manage.py test trade_gui` | 2 tests passed |
| `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-gui-demo.ps1 -ResetData -NoBuild -SkipInstall` | Exposed fresh-volume migration race; Compose fixed |
| `powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\run-gui-demo.ps1 -NoBuild -SkipInstall` | Final artifact run: 53 pass, 4 GUI gaps |
| `node --check .\scripts\gui-simulator-demo.cjs` | Passed |
| PowerShell parser check for `run-gui-demo.ps1` | Passed |
| `docker compose config` | Passed after command/readiness fixes |
| `git diff --check` | Passed (line-ending notices only) |

The Docker integration e2e suite was not rerun because the user-provided baseline already passes and this draft changed no Go/Rust/backend business logic. The final run exercised the full production-shaped GUI path repeatedly, including settlement and invariants.
