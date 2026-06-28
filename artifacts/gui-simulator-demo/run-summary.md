# GUI simulator demo run summary

- Run ID: `20260627122639`
- Assertions: 53 passed, 4 failed
- Video: `video/gui-simulator-qa.webm`
- Initial seller stack: 100
- Final seller stack: 75
- Initial/final total wallet ISK: 4000000/4000000
- Initial/final total item quantity: 180/180
- Final open trades: 0

| Result | Check | Evidence |
|---|---|---|
| PASS | Homepage loads with all seven seeded actions | 7/7 visible |
| PASS | Every seeded action exposes a tooltip | 7/7 tooltips populated |
| FAIL | Neocom shortcuts are functional controls | [{"tag":"DIV","role":null,"tabIndex":-1},{"tag":"DIV","role":null,"tabIndex":-1},{"tag":"DIV","role":null,"tabIndex":-1},{"tag":"DIV","role":null,"tabIndex":-1}] |
| PASS | Initial state has a readable empty response | response panel says no interaction sent |
| PASS | Browser refresh preserves a working simulator | loaded http://127.0.0.1:8000/ |
| PASS | Seller issues a valid market sell order | {"trade_instance_id":"84e1c535-61a6-46db-a3de-497d761f14d0","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":4,"remaining_quantity":4,"unit_price_isk":25,"trade_state":"OPEN"} |
| FAIL | External Request ID field is propagated by the GUI packet | raw packet external_request_id=absent |
| PASS | Buyer accepts the full market order | {"trade_instance_id":"84e1c535-61a6-46db-a3de-497d761f14d0","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":4,"remaining_quantity":0,"unit_price_isk":25,"trade_state":"COMPLETED"} |
| PASS | Full acceptance transfers items and ISK exactly once | buyer items +4; seller ISK +100; buyer ISK -100 |
| PASS | Partial acceptance keeps the order open | {"trade_instance_id":"1f142bb8-dc29-4c86-bc93-863fb8053346","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":10,"remaining_quantity":6,"unit_price_isk":7,"trade_state":"OPEN"} |
| PASS | Final partial acceptance completes without a phantom order | {"trade_instance_id":"1f142bb8-dc29-4c86-bc93-863fb8053346","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":10,"remaining_quantity":0,"unit_price_isk":7,"trade_state":"COMPLETED"} |
| PASS | Seller cancels an outstanding order | {"trade_instance_id":"4d0e27e8-994b-459f-86c1-72353bba596b","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":5,"remaining_quantity":0,"unit_price_isk":9,"trade_state":"CANCELLED"}; seller refund=5 |
| PASS | Cancel after partial fill preserves accepted quantity | buyer retained 3; seller refund=5 |
| PASS | Reject issue with zero quantity | invalid_argument: invalid_argument: quantity must be greater than zero |
| PASS | Reject issue with negative quantity | invalid_argument: invalid_argument: quantity must be greater than zero |
| PASS | Reject issue above owned quantity | invalid_argument: invalid_argument: item stack quantity is lower than requested issue quantity |
| PASS | Reject issue with zero price | invalid_argument: invalid_argument: unit_price_isk must be greater than zero |
| PASS | Reject issue with negative price | invalid_argument: invalid_argument: unit_price_isk must be greater than zero |
| PASS | Reject issue with blank quantity | invalid_argument: invalid_argument: quantity must be greater than zero |
| PASS | Reject issue with blank price | invalid_argument: invalid_argument: unit_price_isk must be greater than zero |
| PASS | Reject issue with missing seller | invalid_argument: invalid_argument: item stack owner must match issued_by_capsuleer_id |
| PASS | Reject issue with missing item stack | invalid_argument: invalid_argument: item_stack_id is required |
| PASS | Reject non-numeric quantity payload | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal string into Go struct field tradeGUIInput.input.quantity of type int64 |
| PASS | Reject extremely large quantity | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal number 9.223372036854776e+25 into Go struct field tradeGUIInput.input.quantity of type int64 |
| PASS | Reject extremely large price | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal number 9.223372036854776e+25 into Go struct field tradeGUIInput.input.unit_price_isk of type int64 |
| PASS | Reject item offered by the wrong owner | invalid_argument: invalid_argument: item stack owner must match issued_by_capsuleer_id |
| PASS | Reject item projection at the wrong station | invalid_argument: invalid_argument: item_stack station_id does not match canonical item stack |
| PASS | Malformed extra JSON is rejected client-side | Invalid JSON in extra payload: SyntaxError: Expected property name or '}' in JSON at position 1 (line 1 column 2) |
| PASS | Reject accept with zero quantity | invalid_argument: invalid_argument: quantity_requested must be greater than zero |
| PASS | Reject accept with negative quantity | invalid_argument: invalid_argument: quantity_requested must be greater than zero |
| PASS | Reject accept above remaining quantity | failed_precondition: failed_precondition: item_stack_escrow 0bbe4d40-f506-4d14-9db3-3b98b557321c has 3, requested 4 |
| PASS | Reject seller accepting own order | invalid_argument: invalid_argument: buyer and seller must differ |
| PASS | Reject wallet owned by another player | failed_precondition: failed_precondition: buyer_wallet_id is not owned by buyer_capsuleer_id |
| PASS | Reject accept with missing wallet | failed_precondition: failed_precondition: load wallet : ERROR: invalid input syntax for type uuid: "" (SQLSTATE 22P02) |
| PASS | Reject non-numeric accept quantity | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal string into Go struct field tradeGUIInput.input.quantity_requested of type int64 |
| PASS | Reject extremely large accept quantity | invalid_argument: invalid_argument: decode trade GUI packet: json: cannot unmarshal number 9.223372036854776e+25 into Go struct field tradeGUIInput.input.quantity_requested of type int64 |
| PASS | Reject accept with insufficient ISK | failed_precondition: failed_precondition: failed_precondition: wallet 00000000-0000-4000-8000-000000002002 has 999821, requested 1200000 |
| PASS | Reject accept of a cancelled trade | failed_precondition: failed_precondition: trade is cancelled |
| PASS | Reject cancelling an already cancelled trade | failed_precondition: failed_precondition: trade is cancelled |
| PASS | Reject accept of a completed trade | failed_precondition: failed_precondition: trade is completed |
| PASS | Reject cancelling a completed trade | failed_precondition: failed_precondition: trade is completed |
| PASS | Reject accept with missing trade ID | invalid_argument: invalid_argument: trade_instance_id is required |
| PASS | Reject cancellation by a non-seller | permission_denied: permission_denied: only the trade issuer can cancel this trade |
| FAIL | Role-based controls are enforced in GUI visibility | all 7 action buttons remain visible for every typed capsuleer ID |
| PASS | Double-click issue creates one trade and one settlement | trade delta=1; settlements=1 |
| PASS | Double-click accept settles once | buyer items +2; buyer ISK -26; settlements=1 |
| PASS | Double-click cancel refunds once | seller items +3; settlements=1 |
| PASS | Immediate refresh cannot duplicate an in-flight issue | settlements=1; trade delta=1 |
| PASS | Contract create and accept controls execute the lifecycle | {"trade_instance_id":"049de09a-fb98-4e21-b85b-c08b8d024e23","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":1,"remaining_quantity":0,"unit_price_isk":10,"trade_state":"COMPLETED"} |
| PASS | Direct offer and accept controls execute the lifecycle | {"trade_instance_id":"dac5bcdb-c289-4874-b605-3088f6016dff","issuer_id":1001,"item_type_id":34,"station_id":60003760,"total_quantity":1,"remaining_quantity":0,"unit_price_isk":11,"trade_state":"COMPLETED"} |
| PASS | Two tabs racing to accept settle exactly once | accepted responses=1; buyer items +2; buyer ISK -38 |
| PASS | Market outage produces visible GUI feedback | failed: timed out |
| FAIL | Action button disables while a request is in flight | button enabled=true |
| PASS | GUI recovers after Market restarts | accepted; settlement=3549ba33-e94b-4e25-948f-2d526fd3965f |
| PASS | Wallet conservation invariant holds | 4000000 -> 4000000 |
| PASS | Item quantity conservation invariant holds | 180 -> 180 |
| PASS | Demo leaves no open trades | open trades=0 |
