# Shop mapping: how the shop demo sits on the unchanged backend

*Owner: Miti. Source: SyncShield Main Build Plan v1.1, section 4.*

The backend keeps its domain-neutral names (`/v1/reports`, `severity`, `status`...). Only the
frontend adapter (`dashboard/src/domain/shop.js`, later `web/src/domain/shop.ts`), the fixtures and
`scripts/loadgen.py` speak "shop". **The UI renders what the API returns. It never recomputes
status, priority or conflicts.**

## Field mapping

| Engine field | Shop label | Concurrent rule (policy v1) | Review? |
|---|---|---|---|
| `title` | Cart owner (Alice) | Latest by HLC | No |
| `description` | Delivery note | Latest by HLC | No |
| `category` | Hidden, always `cart` | Latest by HLC | No |
| `severity` 1-5 | Delivery speed: 1 Standard, 2 Plus, 3 Express, 4 Same-day, 5 Priority | Highest | Receipt only |
| `status` | open = Shopping, assigned = Checking out, resolved = Paid, closed = Order placed, cancelled = Cancelled | Most active wins (lowest on the ladder), latest breaks ties | Yes |
| `assigned_team` | Fulfilment centre: Bhiwandi, Chakan, Gurgaon | Earliest HLC, flag DOUBLE_DISPATCH (shown as "double fulfilment") | Yes |
| `location` | Delivery pin | Latest HLC, review if candidates are 200 m or more apart | If far |
| `people_estimate` | Not used | Highest, never summed | n/a |
| `notes` | Cart activity, one NOTE per item added | Union by event id, same `op_id` collapses | n/a |

## Cart item events

A NOTE whose text is a small JSON string (2000 characters at most):

```json
{"kind":"ADD","sku":"FCOFF-500","name":"Filter coffee 500 g","qty":1,"price":449,"user":"Alice"}
```

- Quantity of a SKU = number of **distinct** `op_id`s among its ADD notes, times `qty`. A retried add never doubles an item.
- There is no remove-from-cart in v1 (it would need add-wins or remove-wins semantics). Checkout, cancel and the status ladder are the "close" operations.
- A cart id is the client-generated `report_id` (`R-` plus 8 hex characters). The UI may show it as `C-xxxx`.
- Until the backend returns the event's `op_id` in note views, the adapter also stores `op` inside the note JSON and dedupes on that.

## Priority lanes (computed by the engine, shown by the UI)

| Lane | Events |
|---|---|
| P0 critical | status to Paid / Order placed / Cancelled; delivery speed raised to 4 or more; fulfilment centre assigned; any RESOLVE; CREATE with speed 4 or more |
| P1 normal | everything else |
| P2 bulk | cart-activity NOTEs and bulk seeds |

The UI must show the event's **stored** priority. Note: a mock may treat Express (3) as critical;
the real rule is severity 4 or more.

## CLOSE_VS_NEW_INFO in shop terms

"Order placed" on one node and a new item (or a Same-day upgrade) on another node, neither having
seen the other, opens a review: *ship with the new item / refund and re-order / keep as is*.
The status stays "Order placed" on every node until a supervisor decides.

## Seed data

Synthetic only. Shoppers Alice, Bob, Chitra, Dev, Esha; products in `fixtures/carts.json`
(Appendix B). Regenerate fixtures with `python scripts/gen_shop_fixtures.py`.
