# Calibration record — policy 1.0.0

Calibrated on 2026-09-25 against the live sandbox snapshot `efb956ef…c45338` (9 bikes, 23 parts, no data
issues), captured in `tests/fixtures/live/`. Signed off by Mirza Shahzaib Baig, acting as procurement.
Reproduce with `uv run lato calibrate` (offline, from the fixtures) or `uv run lato calibrate --live`.

## What the data showed
- **Structure.** Every bike uses exactly one part from each of 8 categories (frame, wheels, pedals, front
  derailleur, rear derailleur, fork, seat, gear set). Every part→bike reference matches a product; no orphans.
- **Fan-out.** 4 parts are used by all 9 bikes (fork, rear derailleur, gear set, seat); then 1 part ×4, 3 ×3,
  8 ×2, 7 ×1. CRITICAL (≥ 50% of bikes) = 4/23 = 17%, inside the 15–35% target, so `t1_min_share` stays 0.5.
- **Scale.** Part stock 7–99; bike stock 9–96. Every part has `PartAmountPerCrate = 97`.
- **Not in the data.** Units per bike, demand, lead time, cost. `NumberOfParts = 47` per bike counts components
  outside this inventory, so the agent can only protect these 8 categories.

## Decisions

| Parameter | Value | Why |
|---|---|---|
| `k_builds_per_model_per_cycle` | **5** | Orders exactly the three universal parts that are genuinely short (fork 16, rear derailleur 19, gear set 26 → 0.4–0.6 cycles of cover) plus three shared parts, 461 units in total. K = 8 (the "parts hold ~3 cycles" anchor) would order 117 seats with 99 in stock; K = 3 under-protects if demand is higher. The "median bike stock" anchor (80) is not a usable reading of per-cycle builds. |
| `lot_mode` | **unit** | Full crates of 97 roughly double the units ordered (e.g. 35 wheels → 97) with no shipping-cost data to justify it. Conflicts with "don't tie up cash". |
| Wheels and pedals | **2 per bike** | One unit = one wheel / one pedal. Set in `config/bom_overrides.yaml` for all 9 wheel/pedal parts. |
| `auto_max_units_per_po` | 100 | Larger orders need a human. At K = 5 the largest non-critical order is 66. |
| `hard_max_units_per_po` | **300** | About 2× the largest order-up-to level (135). The sandbox accepted a 1,000,000-unit PO, so this cap is the only upper bound. |
| Auto budget per run | 5 POs / 300 units | At K = 5 the auto lines total 3 POs / 117 units. |
| `fg_low` | **36** | 25th percentile of bike stock. Exposed today: Carrera SubwayE (9), CityWide Chill (21). |

## Result at the frozen settings (nothing on order)

| Part | Tier | Stock | Cycle demand | Reorder point | Order-up-to | Order | Route |
|---|---|---|---|---|---|---|---|
| Hydraulic Suspension Fork | CRITICAL | 16 | 45 | 90 | 135 | 119 | human |
| Rear Derailleur Mechanism | CRITICAL | 19 | 45 | 90 | 135 | 116 | human |
| Precision Gear Set | CRITICAL | 26 | 45 | 90 | 135 | 109 | human |
| Anti-Slip Pedals | SHARED | 34 | 40 | 60 | 100 | 66 | auto |
| Lightweight Alloy Wheels | SHARED | 15 | 20 | 30 | 50 | 35 | auto |
| Lightweight Steel Frame | SHARED | 22 | 15 | 23 | 38 | 16 | auto |

**Critical vs merely low.** The Performance Front Derailleur has the lowest stock of any part (7) but is used by
one bike: 1.4 cycles of cover, above its reorder point of 5, so it is held. The fork has more than twice the
stock (16) but serves all 9 bikes: 0.36 cycles of cover, so it is ordered first and sent to a human.

Routes in this table are the calibration approximation; the engine (M4) adds finished-bike exposure, auto
budget and data flags.

## Known limits
- K is an assumption, not a measurement. Replace it with real consumption as soon as snapshot history or
  sales data exist (the fixtures make this a config change, not a code change).
- Lead time is assumed equal for every supplier (1 cycle). Supplier concentration (Mavic 5 parts, Shimano 4)
  is shown in the calibration report, not priced into buffers.
- The receipt signal (on-hand stock rising after an order) cannot be tested in the sandbox, where stock never moves.
