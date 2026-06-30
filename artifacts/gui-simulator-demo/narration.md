# GUI simulator QA narration

Run: 20260630141728

## 1. 1. Simulator loaded

The one-page client exposes seven seeded controls and an explicit empty gateway-response state.

## 2. 2. Refresh stability

A full browser reload returns to a clean form. No selected trade or persisted player context exists in this GUI.

## 3. 3. Happy path — order issued

The GUI packet traverses Quilkin and the gateway. PostgreSQL confirms an OPEN order with four units in escrow.

## 4. 4. Happy path — completed

Full acceptance completes the order, adds four Tritanium to the buyer stack, credits 100 ISK, and debits 100 ISK.

## 5. 5. Partial fill — order issued

A ten-unit order is created to validate remaining quantity and multi-step completion.

## 6. 6. Partial fill — six remain

The first buyer action fills four units. Database evidence shows OPEN with six units remaining.

## 7. 7. Partial fill — completed

The remaining six units settle and the same trade row moves to COMPLETED; no duplicate trade row is created.

## 8. 8. Cancel outstanding order

Cancellation marks the trade CANCELLED and returns all five unfilled items to the seller stack.

## 9. 9. Cancel after partial fill

Three purchased units remain with the buyer; only the five unfilled units are refunded before the order is cancelled.

## 10. 10. Invalid issue matrix

Zero, negative, excessive, missing, non-numeric, huge, wrong-owner, and wrong-station inputs are rejected without creating trades.

## 11. 11. Invalid accept matrix

Invalid quantities, ownership, wallet, funds, missing IDs, cancelled trades, and completed trades return specific errors without corrupting state.

## 12. 12. Identity and authorization

The authenticated UDP edge rejects a principal mismatch; this development-only harness intentionally keeps every seeded test action visible.

## 13. 13. Duplicate and retry safety

Rapid create, buy, and cancel clicks produce one settlement each. Immediate refresh produces at most one order.

## 14. 14. Item-exchange controls

The contract-labeled controls map to the same settlement lifecycle and complete a one-unit item exchange.

## 15. 15. Direct-trade controls

The direct-trade Offer and Accept buttons also reach settlement and complete exactly one trade row.

## 16. 16. Two-tab acceptance race

Two browser tabs submit different acceptance IDs against the same order. One succeeds; the other is rejected; items and ISK move once.

## 17. 17. Dependency outage

With Market stopped, the response reports downstream unavailability and the action button stays disabled until the request completes.

## 18. 18. QA summary

57 checks pass. 0 GUI/UX checks expose real gaps; backend conservation and duplicate-settlement checks hold.
