# GUI simulator demo run summary

- Run ID: `20260630141728`
- Git commit: `a04220d11ab6185edd84a3bbc0cebdf9f3cec225`
- Dirty worktree: yes (67 paths)
- CI run: local
- pnpm lock SHA-256: `0d546381f184c8e4c6ff05fe331723e3a60ae672f3933fb24676523d40f90754`
- Playwright / Node: 1.55.0 / v24.14.0
- Assertions: 57 passed, 0 failed
- Video: `video/gui-simulator-qa.webm`
- Initial seller stack: 75
- Final seller stack: 50
- Initial/final total wallet ISK: 4000000/4000000
- Initial/final total item quantity: 180/180
- Final open trades: 0

| Result | Check | Evidence |
|---|---|---|
| PASS | Homepage loads with all seven seeded actions | 7/7 visible |
| PASS | Every seeded action exposes a tooltip | 7/7 tooltips populated |
| PASS | Neocom shortcuts are functional controls | [{"tag":"BUTTON","role":null,"tabIndex":0},{"tag":"BUTTON","role":null,"tabIndex":0},{"tag":"BUTTON","role":null,"tabIndex":0},{"tag":"BUTTON","role":null,"tabIndex":0}] |
| PASS | Initial state has a readable empty response | response panel says no interaction sent |
| PASS | Browser refresh preserves a working simulator | loaded http://127.0.0.1:8000/ |
| PASS | Seller issues a valid market sell order | {"trade_instance_id":"68a58e65-7a78-491b-b552-d4899edcffe7","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":4,"remaining_quantity":4,"unit_price_isk":25,"trade_state":"OPEN"} |
| PASS | External Request ID field is propagated by the GUI packet | raw packet external_request_id=gui-demo-20260630141728-happy-issue |
| PASS | Buyer accepts the full market order | {"trade_instance_id":"68a58e65-7a78-491b-b552-d4899edcffe7","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":4,"remaining_quantity":0,"unit_price_isk":25,"trade_state":"COMPLETED"} |
| PASS | Full acceptance transfers items and ISK exactly once | buyer items +4; seller ISK +100; buyer ISK -100 |
| PASS | Partial acceptance keeps the order open | {"trade_instance_id":"761abd79-9243-435a-86f5-a8111f1a9638","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":10,"remaining_quantity":6,"unit_price_isk":7,"trade_state":"OPEN"} |
| PASS | Final partial acceptance completes without a phantom order | {"trade_instance_id":"761abd79-9243-435a-86f5-a8111f1a9638","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":10,"remaining_quantity":0,"unit_price_isk":7,"trade_state":"COMPLETED"} |
| PASS | Seller cancels an outstanding order | {"trade_instance_id":"ef24acd0-cfbb-4ba8-afac-7a3000d4009f","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":5,"remaining_quantity":0,"unit_price_isk":9,"trade_state":"CANCELLED"}; seller refund=5 |
| PASS | Cancel after partial fill preserves accepted quantity | buyer retained 3; seller refund=5 |
| PASS | Reject issue with zero quantity | invalid_argument: invalid_argument: quantity must be greater than zero |
| PASS | Reject issue with negative quantity | invalid_argument: invalid_argument: quantity must be greater than zero |
| PASS | Reject issue above owned quantity | invalid_argument: invalid_argument: item stack quantity is lower than requested issue quantity |
| PASS | Reject issue with zero price | invalid_argument: invalid_argument: unit_price_isk must be greater than zero |
| PASS | Reject issue with negative price | invalid_argument: invalid_argument: unit_price_isk must be greater than zero |
| PASS | Reject issue with blank quantity | invalid_argument: invalid_argument: quantity must be greater than zero |
| PASS | Reject issue with blank price | invalid_argument: invalid_argument: unit_price_isk must be greater than zero |
| PASS | Reject issue with missing seller | failed: issued_by_capsuleer_id must be positive |
| PASS | Reject issue with missing item stack | invalid_argument: invalid_argument: item_stack_id is required |
| PASS | Reject non-numeric quantity payload | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal string into Go struct field tradeGUIInput.input.quantity of type int64 |
| PASS | Reject extremely large quantity | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal number 9.223372036854776e+25 into Go struct field tradeGUIInput.input.quantity of type int64 |
| PASS | Reject extremely large price | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal number 9.223372036854776e+25 into Go struct field tradeGUIInput.input.unit_price_isk of type int64 |
| PASS | Reject item offered by the wrong owner | invalid_argument: invalid_argument: item stack owner must match issued_by_capsuleer_id |
| PASS | Reject item projection at the wrong station | invalid_argument: invalid_argument: item_stack station_id does not match canonical item stack |
| PASS | Malformed extra JSON is rejected client-side | Invalid JSON in extra payload: SyntaxError: Expected property name or '}' in JSON at position 1 (line 1 column 2) |
| PASS | Reject accept with zero quantity | invalid_argument: invalid_argument: quantity_requested must be greater than zero |
| PASS | Reject accept with negative quantity | invalid_argument: invalid_argument: quantity_requested must be greater than zero |
| PASS | Reject accept above remaining quantity | failed_precondition: failed_precondition: item_stack_escrow 37a9a5a2-647e-47a2-ab7b-84a4acbb1308 has 3, requested 4 |
| PASS | Reject seller accepting own order | invalid_argument: invalid_argument: buyer and seller must differ |
| PASS | Reject wallet owned by another player | failed_precondition: failed_precondition: buyer_wallet_id is not owned by buyer_capsuleer_id |
| PASS | Reject accept with missing wallet | failed_precondition: failed_precondition: load wallet : ERROR: invalid input syntax for type uuid: "" (SQLSTATE 22P02) |
| PASS | Reject non-numeric accept quantity | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal string into Go struct field tradeGUIInput.input.quantity_requested of type int64 |
| PASS | Reject extremely large accept quantity | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal number 9.223372036854776e+25 into Go struct field tradeGUIInput.input.quantity_requested of type int64 |
| PASS | Reject accept with insufficient ISK | failed_precondition: failed_precondition: failed_precondition: wallet 00000000-0000-4000-8000-000000002002 has 999545, requested 1200000 |
| PASS | Reject accept of a cancelled trade | failed_precondition: failed_precondition: trade is cancelled |
| PASS | Reject cancelling an already cancelled trade | failed_precondition: failed_precondition: trade is cancelled |
| PASS | Reject accept of a completed trade | failed_precondition: failed_precondition: trade is completed |
| PASS | Reject cancelling a completed trade | failed_precondition: failed_precondition: trade is completed |
| PASS | Reject accept with missing trade ID | invalid_argument: invalid_argument: trade_instance_id is required |
| PASS | Reject cancellation by a non-seller | permission_denied: permission_denied: only the trade issuer can cancel this trade |
| PASS | Multi-principal test controls remain available | 7/7 test actions visible |
| PASS | Double-click issue creates one trade and one settlement | trade delta=1; settlements=1 |
| PASS | Double-click accept settles once | buyer items +2; buyer ISK -26; settlements=1 |
| PASS | Double-click cancel refunds once | seller items +3; settlements=1 |
| PASS | Immediate refresh commits exactly one idempotent issue | settlements=1; trade delta=1 |
| PASS | Contract create and accept controls execute the lifecycle | {"trade_instance_id":"28bcadd5-1bca-41e3-8dfd-111e78eeaa2f","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":1,"remaining_quantity":0,"unit_price_isk":10,"trade_state":"COMPLETED"} |
| PASS | Direct offer and accept controls execute the lifecycle | {"trade_instance_id":"8b621b0c-7f96-40e5-9f18-651cf52f28f5","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":1,"remaining_quantity":0,"unit_price_isk":11,"trade_state":"COMPLETED"} |
| PASS | Two tabs racing to accept settle exactly once | accepted responses=1; buyer items +2; buyer ISK -38 |
| PASS | Market outage produces visible GUI feedback | downstream_unavailable: downstream unavailable |
| PASS | Action button disables while a request is in flight | button enabled=false |
| PASS | GUI recovers after Market restarts | accepted; settlement=0bd85d16-379a-4d8c-b0dd-8b19f829f77b |
| PASS | Wallet conservation invariant holds | 4000000 -> 4000000 |
| PASS | Item quantity conservation invariant holds | 180 -> 180 |
| PASS | Demo leaves no open trades | open trades=0 |
