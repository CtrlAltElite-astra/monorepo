# Later

Things deliberately left for later. Nothing here blocks the demo.

## README screenshots

The README no longer embeds pictures: the old ones in `docs/screenshots/` show the first dashboard, not the current
`frontend-new` console. To add later (run the stack, then capture at about 1600 px wide):

1. Landing page hero (`http://localhost:5175`).
2. Console overview: shop on the left, three healthy region cards on the right (`/demo`).
3. A crashed region: one card red and offline, the route line saying "Serving from Pune · Mumbai is down".
4. Recovery: the "Recovered" tile above zero after a crash with replication paused.
5. Review: the resolve dialog showing "Order placed" against "New item added elsewhere".
6. Prove it: the result table with identical digests on all three regions.
7. Security tab after Tamper: three forged events rejected.

Put them in `docs/screenshots/` and add them back to the README's demo section.

## Also open

- The judges found the "why" explanation cluttered: simplify the landing page story.
- Decide whether to keep `web/` and `dashboard/` now that `frontend-new` is the demo frontend.
- Re-run the real-backend browser test after the latest UI changes (remove from cart, new cart after an order).
