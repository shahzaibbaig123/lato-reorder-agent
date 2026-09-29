# Reorder plan bfbad8c1f90c

Created 2026-09-25 16:07 UTC. Mode `SANDBOX`, LLM `claude-haiku-4-5 (prompt 34cc556cbbf23ba2)`, policy `1.0.0` (`3315430fb6b5ff46`), snapshot `efb956ef6b945132`.

**0 orders, 0 units:** 0 need a human, 0 auto-approved (0 units), 1 blocked. 23 parts reviewed.

## Run flags

- SUPPLIER_CONCENTRATION: Mavic supplies 3 shared/critical parts
- SUPPLIER_CONCENTRATION: Look supplies 3 shared/critical parts
- SUPPLIER_CONCENTRATION: Shimano supplies 3 shared/critical parts

## Orders

### Precision Gear Set: BLOCKED

Blocked: this CRITICAL part is used by all 9 bike models in the catalogue. An earlier purchase order for 109 units is in flight with an unknown outcome. No new order can be placed until that PO is resolved.

The part cannot be ordered. The open PO outcome must be confirmed before procurement can assess whether a second order is needed. Once resolved, reorder triggers when inventory position falls to 90 or below.

- Rules: TIER-1, RTE-B-INFLIGHT
- `used by 9 of 9 bike models (share 1.00)`
- `D = K x q x f = 5 x 1 x 9 = 45 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 1) x 45) = 90`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 1) x 45) = 135`
- `IP = on hand + on order = 26 + 109 = 135`

## No order

### Advanced Front Derailleur: NO_ORDER

No order: this part is used by 2 of 9 bike models and inventory position 97 is well above the reorder point of 15, so stock is adequate. The part is flagged as excess.

An order would trigger only if inventory position falls to 15 or below. Current stock of 97 provides 9.7 cycles of cover at the assumed consumption rate.

- Rules: TIER-2, HOLD-ABOVE-ROP
- Flags: EXCESS
- `used by 2 of 9 bike models (share 0.22)`
- `D = K x q x f = 5 x 1 x 2 = 10 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 10) = 15`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 10) = 25`
- `IP = on hand + on order = 97 + 0 = 97`

### All-Terrain Carbon Frame: NO_ORDER

No order: only the EveryDay model uses this frame and inventory position 28 is well above the reorder point of 5, so stock is adequate. The EXCESS flag confirms ample supply.

An order would trigger only if inventory position falls to 5 or below. Current stock of 28 covers 5.6 cycles of demand.

- Rules: TIER-3, HOLD-ABOVE-ROP
- Flags: EXCESS
- `used by 1 of 9 bike models (share 0.11)`
- `D = K x q x f = 5 x 1 x 1 = 5 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0) x 5) = 5`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0) x 5) = 10`
- `IP = on hand + on order = 28 + 0 = 28`

### Anti-Slip Pedals: NO_ORDER

No order: four bike models depend on this part and an open purchase order of 66 units will bring inventory position to 100, which meets the order-up-to level. Reordering now would exceed stock targets.

Inventory position of 100 equals the order-up-to level of 100. An open PO already covers the gap between current stock and the reorder point of 60, so no additional order is needed.

- Rules: TIER-2, HOLD-OPEN-PO
- `used by 4 of 9 bike models (share 0.44)`
- `D = K x q x f = 5 x 2 x 4 = 40 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 40) = 60`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 40) = 100`
- `IP = on hand + on order = 34 + 66 = 100`

### Carbon Fiber Frame: NO_ORDER

No order: only the Carrera Subway model uses this frame and inventory position 35 is well above the reorder point of 5, so stock is adequate.

An order triggers when inventory position falls to 5 or below. Current stock of 35 covers 7 cycles of demand.

- Rules: TIER-3, HOLD-ABOVE-ROP
- Flags: EXCESS
- `used by 1 of 9 bike models (share 0.11)`
- `D = K x q x f = 5 x 1 x 1 = 5 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0) x 5) = 5`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0) x 5) = 10`
- `IP = on hand + on order = 35 + 0 = 35`

### Durable Alloy Wheels: NO_ORDER

No order: only the Carrera Subway model uses this part, and inventory position 13 is above the reorder point of 10, so stock is adequate for the current cycle.

An order would trigger when inventory position falls to 10 or below. Current stock of 13 covers 1.3 cycles of demand at 10 units per cycle.

- Rules: TIER-3, HOLD-ABOVE-ROP
- `used by 1 of 9 bike models (share 0.11)`
- `D = K x q x f = 5 x 2 x 1 = 10 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0) x 10) = 10`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0) x 10) = 20`
- `IP = on hand + on order = 13 + 0 = 13`

### Endurance Alloy Wheels: NO_ORDER

No order: this part is used by only one bike model and inventory position 78 is well above the reorder point of 10, so stock is adequate and no reorder is needed.

An order would trigger only if inventory position falls to 10 or below. Current stock of 78 covers 7.8 cycles of demand.

- Rules: TIER-3, HOLD-ABOVE-ROP
- Flags: EXCESS
- `used by 1 of 9 bike models (share 0.11)`
- `D = K x q x f = 5 x 2 x 1 = 10 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0) x 10) = 10`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0) x 10) = 20`
- `IP = on hand + on order = 78 + 0 = 78`

### Endurance Carbon Frame: NO_ORDER

No order: this frame is used by 2 of 9 bike models and inventory position 30 is above the reorder point of 15, so stock is adequate. However, one dependent model (CityWide Chill) has low finished-bike stock.

An order triggers when inventory position falls to 15 or below. Current stock of 30 provides 3 cycles of cover at the current demand rate of 10 units per cycle.

- Rules: TIER-2, HOLD-ABOVE-ROP
- Flags: FG_EXPOSED
- `used by 2 of 9 bike models (share 0.22)`
- `D = K x q x f = 5 x 1 x 2 = 10 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 10) = 15`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 10) = 25`
- `IP = on hand + on order = 30 + 0 = 30`

### Front Derailleur Mechanism: NO_ORDER

No order: only the Carrera Subway model uses this front derailleur and inventory position 34 is well above the reorder point of 5, so stock is adequate.

An order would trigger only if inventory position falls to 5 or below. Current stock of 34 provides 6.8 cycles of cover.

- Rules: TIER-3, HOLD-ABOVE-ROP
- Flags: EXCESS
- `used by 1 of 9 bike models (share 0.11)`
- `D = K x q x f = 5 x 1 x 1 = 5 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0) x 5) = 5`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0) x 5) = 10`
- `IP = on hand + on order = 34 + 0 = 34`

### Gel Comfort Seat: NO_ORDER

No order: this part is used by all 9 bike models in the catalogue, making it CRITICAL. Inventory position of 99 units is above the reorder point of 90, so stock is adequate for now despite two dependent models running low on finished bikes.

An order triggers when inventory position falls to 90 units or below. Current position of 99 is sufficient to cover 2.2 cycles of demand at the CRITICAL safety level.

- Rules: TIER-1, HOLD-ABOVE-ROP
- Flags: WATCH
- `used by 9 of 9 bike models (share 1.00)`
- `D = K x q x f = 5 x 1 x 9 = 45 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 1) x 45) = 90`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 1) x 45) = 135`
- `IP = on hand + on order = 99 + 0 = 99`

### High-Performance Derailleur: NO_ORDER

No order: this part is used by 3 of 9 bike models and inventory position 64 is well above the reorder point of 23, so stock is adequate. One dependent model has low finished-bike stock, but the part itself is not at risk.

An order would trigger when inventory position falls to 23 or below. Current stock of 64 covers 4.27 cycles of demand at 15 units per cycle.

- Rules: TIER-2, HOLD-ABOVE-ROP
- `used by 3 of 9 bike models (share 0.33)`
- `D = K x q x f = 5 x 1 x 3 = 15 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 15) = 23`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 15) = 38`
- `IP = on hand + on order = 64 + 0 = 64`

### High-Traction Pedals: NO_ORDER

No order: this part is used by 2 of 9 bike models and inventory position 86 is well above the reorder point of 30, so stock is adequate for current demand.

An order would trigger when inventory position falls to 30 or below. Current stock of 86 covers 4.3 cycles of demand at 20 units per cycle.

- Rules: TIER-2, HOLD-ABOVE-ROP
- `used by 2 of 9 bike models (share 0.22)`
- `D = K x q x f = 5 x 2 x 2 = 20 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 20) = 30`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 20) = 50`
- `IP = on hand + on order = 86 + 0 = 86`

### Hydraulic Suspension Fork: NO_ORDER

No order: this fork is used by all 9 bike models in the catalogue, making it CRITICAL. An open purchase order for 119 units is already in flight, bringing inventory position to 135, which is at the order-up-to level. No second order is needed.

Inventory position 135 equals the order-up-to level of 135. The open PO lifts stock above the reorder point of 90, so no additional order triggers. An order will be placed only if inventory position falls to 90 or below.

- Rules: TIER-1, HOLD-OPEN-PO
- `used by 9 of 9 bike models (share 1.00)`
- `D = K x q x f = 5 x 1 x 9 = 45 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 1) x 45) = 90`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 1) x 45) = 135`
- `IP = on hand + on order = 16 + 119 = 135`

### Lightweight Alloy Wheels: NO_ORDER

No order: this part is used by 2 of 9 bike models and an open purchase order for 35 units is already in flight. Combined with 15 units on hand, inventory position of 50 meets the order-up-to level, so no additional stock is needed now.

An order would trigger only if inventory position falls to 30 or below. The open PO lifts current position to 50, which is at the order-up-to target, so no reorder is placed.

- Rules: TIER-2, HOLD-OPEN-PO
- `used by 2 of 9 bike models (share 0.22)`
- `D = K x q x f = 5 x 2 x 2 = 20 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 20) = 30`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 20) = 50`
- `IP = on hand + on order = 15 + 35 = 50`

### Lightweight Steel Frame: NO_ORDER

No order: this frame is used by 3 of 9 bike models and inventory position is 38 units, exactly at the order-up-to level. A purchase order for 16 units is already open, so reordering now would exceed the target stock level.

An order would trigger only if inventory position falls to 23 or below. The open PO will bring stock to target without a second order.

- Rules: TIER-2, HOLD-OPEN-PO
- `used by 3 of 9 bike models (share 0.33)`
- `D = K x q x f = 5 x 1 x 3 = 15 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 15) = 23`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 15) = 38`
- `IP = on hand + on order = 22 + 16 = 38`

### Performance Front Derailleur: NO_ORDER

No order: only the EveryDay model uses this derailleur and inventory position 7 is above the reorder point of 5, so stock is adequate for the current cycle.

An order would trigger only if inventory position falls to 5 or below. Current position of 7 covers 1.4 cycles of demand.

- Rules: TIER-3, HOLD-ABOVE-ROP
- `used by 1 of 9 bike models (share 0.11)`
- `D = K x q x f = 5 x 1 x 1 = 5 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0) x 5) = 5`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0) x 5) = 10`
- `IP = on hand + on order = 7 + 0 = 7`

### Pro-Grade Front Derailleur: NO_ORDER

No order: this derailleur is used by 2 of 9 bike models and inventory position 51 is well above the reorder point of 15, so stock is adequate. One dependent model has low finished-bike stock, but the part itself is not at risk.

An order would trigger only if inventory position falls to 15 or below. Current stock of 51 covers 5.1 cycles of demand at the SHARED safety level.

- Rules: TIER-2, HOLD-ABOVE-ROP
- Flags: EXCESS, FG_EXPOSED
- `used by 2 of 9 bike models (share 0.22)`
- `D = K x q x f = 5 x 1 x 2 = 10 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 10) = 15`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 10) = 25`
- `IP = on hand + on order = 51 + 0 = 51`

### Racing Alloy Wheels: NO_ORDER

No order: this part is used by 2 of 9 bike models and inventory position 92 is well above the reorder point of 30, so stock is adequate. One dependent model has low finished-bike stock, but the part itself does not yet need reordering.

An order triggers when inventory position falls to 30 or below. Current stock of 92 covers 4.6 cycles of demand at 20 units per cycle.

- Rules: TIER-2, HOLD-ABOVE-ROP
- Flags: FG_EXPOSED
- `used by 2 of 9 bike models (share 0.22)`
- `D = K x q x f = 5 x 2 x 2 = 20 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 20) = 30`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 20) = 50`
- `IP = on hand + on order = 92 + 0 = 92`

### Rear Derailleur Mechanism: NO_ORDER

No order: this part is CRITICAL, used by all 9 bike models in the catalogue. An open purchase order for 120 units is already in flight, bringing inventory position to 139, which is well above the reorder point of 90. A second order is not needed.

Inventory position 139 exceeds the reorder point of 90. The open PO of 120 units is sufficient to maintain cover. An order would trigger only if inventory position falls to 90 or below.

- Rules: TIER-1, HOLD-OPEN-PO
- `used by 9 of 9 bike models (share 1.00)`
- `D = K x q x f = 5 x 1 x 9 = 45 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 1) x 45) = 90`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 1) x 45) = 135`
- `IP = on hand + on order = 19 + 120 = 139`

### Reinforced Alloy Wheels: NO_ORDER

No order: three bike models use these wheels and inventory position 91 is well above the reorder point of 45, so stock is adequate. One dependent model has low finished-bike stock, but the part itself is not at risk.

An order would trigger when inventory position falls to 45 or below. Current position of 91 covers 3.03 cycles of demand at 30 units per cycle.

- Rules: TIER-2, HOLD-ABOVE-ROP
- `used by 3 of 9 bike models (share 0.33)`
- `D = K x q x f = 5 x 2 x 3 = 30 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 30) = 45`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 30) = 75`
- `IP = on hand + on order = 91 + 0 = 91`

### Reinforced Carbon Frame: NO_ORDER

No order: this frame is used by 2 of 9 bike models and inventory position of 70 units is well above the reorder point of 15, providing 7 cycles of cover. Reordering is not yet needed.

An order would trigger when inventory position falls to 15 units or below. Current stock of 70 is sufficient for near-term demand.

- Rules: TIER-2, HOLD-ABOVE-ROP
- Flags: EXCESS
- `used by 2 of 9 bike models (share 0.22)`
- `D = K x q x f = 5 x 1 x 2 = 10 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 10) = 15`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 10) = 25`
- `IP = on hand + on order = 70 + 0 = 70`

### Trail-Ready Pedals: NO_ORDER

No order: this part is used by only one bike model and inventory position 24 is well above the reorder point of 10, so stock is adequate.

An order would trigger when inventory position falls to 10 or below. Current stock of 24 covers 2.4 cycles of demand at 10 units per cycle.

- Rules: TIER-3, HOLD-ABOVE-ROP
- `used by 1 of 9 bike models (share 0.11)`
- `D = K x q x f = 5 x 2 x 1 = 10 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0) x 10) = 10`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0) x 10) = 20`
- `IP = on hand + on order = 24 + 0 = 24`

### Ultra-Grip Pedals: NO_ORDER

No order: this part is used by 2 of 9 bike models and inventory position 93 is well above the reorder point of 30, so stock is adequate. One dependent model has low finished-bike stock, but the part itself does not yet need replenishment.

An order triggers when inventory position falls to 30 or below. Current stock of 93 provides 4.65 cycles of cover at the assumed consumption rate.

- Rules: TIER-2, HOLD-ABOVE-ROP
- Flags: FG_EXPOSED
- `used by 2 of 9 bike models (share 0.22)`
- `D = K x q x f = 5 x 2 x 2 = 20 units/cycle`
- `ROP = ceil((LT + ss) x D) = ceil((1 + 0.5) x 20) = 30`
- `OUT = ceil((LT + R + ss) x D) = ceil((1 + 1 + 0.5) x 20) = 50`
- `IP = on hand + on order = 93 + 0 = 93`
