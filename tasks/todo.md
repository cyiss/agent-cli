# Agent Feedback Fix Plan

Sources: PR #2 (onboarding blockers), PR #3 (execution layer), Telegram bug report (IOC deep-dive)

---

## Phase 1: P0 — Execution Layer (Ship-Blocking)

The IOC bug makes all 6 MM strategies unprofitable by design. Nothing else matters until this is fixed.

### 1.1 Add `order_type` field to `StrategyDecision`
- [x] **File:** `common/models.py`
- [x] Add `order_type: str = "Gtc"` field to `StrategyDecision` (values: `"Gtc"`, `"Alo"`, `"Ioc"`)
- [x] Default to `"Gtc"` — MM strategies rest on the book by default

### 1.2 Fix `OrderManager.update()` to read order_type from decision
- [x] **File:** `cli/order_manager.py` (line 98)
- [x] Change `tif="Ioc"` → `tif=d.order_type`
- [x] Clearing fills (parent/hl_proxy.py) — kept IOC (correct for clearing)
- [x] WOLF runner — kept IOC (directional entries, correct)
- [x] TWAP child slices — kept IOC (correct)
- [x] Emergency closes — kept IOC (correct)

### 1.3 MM strategies — inherit GTC default (no code changes needed)
- [x] `strategies/simple_mm.py` → GTC (default)
- [x] `strategies/avellaneda_mm.py` → GTC (default)
- [x] `strategies/grid_mm.py` → GTC (default)
- [x] `strategies/engine_mm.py` → GTC (default, requires quoting_engine)
- [x] `strategies/regime_mm.py` → GTC (default, requires quoting_engine)
- [x] `strategies/liquidation_mm.py` → GTC (default, requires quoting_engine)
- [x] `strategies/rfq_agent.py` → GTC (default, resting dark quotes)
- [x] `strategies/funding_arb.py` → GTC (default, funding carry)

### 1.4 Directional strategies — explicitly set `order_type="Ioc"`
- [x] `strategies/aggressive_taker.py` — 2 decisions
- [x] `strategies/momentum_breakout.py` — 4 decisions (2 trailing stops + 2 breakouts)
- [x] `strategies/mean_reversion.py` — 2 decisions
- [x] `strategies/hedge_agent.py` — 1 decision
- [x] `strategies/basis_arb.py` — 4 decisions
- [x] `strategies/claude_agent.py` — 1 decision

### 1.5 Tests
- [x] 318 tests passing

---

## Phase 2: High — Order Execution Reliability ✅

### 2.1 Order size precision (`szDecimals`)
- [x] Added `_get_sz_decimals()` to DirectHLProxy with lazy caching
- [x] `place_order()` now rounds size to instrument-specific decimals
- [x] WOLF runner delegates rounding to adapter
- [ ] Affects: all strategies via WOLF and direct `hl trade`

### 2.2 API rate limiting — backoff + batching for movers scan
- [ ] **File:** `modules/movers_engine.py`
- [ ] Add request pacing: batch asset queries or add small delays between calls (229 assets)
- [ ] **File:** `parent/hl_proxy.py`
- [ ] Add exponential backoff on HTTP 429 responses (retry 3x with 1s/2s/4s delays)

### 2.3 Keystore filename case mismatch
- [ ] **File:** `cli/keystore.py`
- [ ] Fix `create_keystore()`: lowercase the address before writing the filename
- [ ] Or fix `load_keystore()`: use case-insensitive file lookup (glob + lowercase compare)
- [ ] Prefer: lowercase at creation time (simpler, one-line fix)

---

## Phase 3: High — Missing Modules & Onboarding

### 3.1 Ship `quoting_engine` or gate it cleanly
- [ ] **Decision needed:** Ship as open-source, or keep proprietary?
- [ ] If shipping: add `quoting_engine/` to the repo
- [ ] If proprietary: add graceful error message in strategy registry with fallback suggestion
- [ ] Update README to clearly mark which strategies need it
- [ ] Update "14 strategies" claim if some are gated

### 3.2 Fund builder wallet on mainnet
- [ ] Fund `0xF8C75F891cb011E2097308b856bEC74f5ea10F20` on HL mainnet
- [ ] Or: make builder fee opt-in (disable by default until wallet is funded)
- [ ] Verify `hl builder approve --mainnet` works after funding

### 3.3 Add `--yes` flag to `hl builder approve`
- [ ] **File:** `cli/commands/builder.py` (or wherever the approve command lives)
- [ ] Add `-y` / `--yes` flag to skip interactive confirmation
- [ ] Auto-confirm when stdin is not a TTY (agent/CI environments)

---

## Phase 4: Medium — Developer Experience

### 4.1 `bootstrap.sh` portability
- [ ] **File:** `scripts/bootstrap.sh`
- [ ] Add `#!/bin/bash` shebang if not present
- [ ] Replace `source` with `.` for POSIX compatibility (or keep bash requirement)
- [ ] Add check for `python3-venv` package, suggest install command if missing

### 4.2 WOLF budget-aware instrument filtering
- [ ] **File:** `modules/wolf_engine.py` (`_evaluate_entries()`)
- [ ] Before evaluating signals, filter out instruments where `margin_per_slot / price < min_order_size`
- [ ] Query HL `metaAndAssetCtxs` for minimum order sizes (can share cache with 2.1)
- [ ] Log filtered instruments so user knows why they were skipped

### 4.3 SDK spot metadata crash workaround
- [ ] **File:** Upstream `hyperliquid-python-sdk` or local monkey-patch
- [ ] Wrap spot token parsing in try/except (may already be done per PR #2)
- [ ] Report upstream if not already done

### 4.4 Programmatic bridge/deposit command (stretch)
- [ ] **New file:** `cli/commands/deposit.py`
- [ ] `hl wallet deposit --chain arbitrum --amount 99`
- [ ] ERC20 approve + transfer to HL bridge contract
- [ ] Requires: user's Arbitrum RPC, funded L1 wallet
- [ ] Lower priority — nice-to-have for full zero-touch onboarding

---

## Execution Order

```
Phase 1 (P0 — IOC fix)     → Branch: fix/execution-layer-gtc
Phase 2 (Order reliability) → Branch: fix/order-reliability
Phase 3 (Onboarding)        → Branch: fix/onboarding-blockers
Phase 4 (DX)                → Branch: improve/dx-polish
```

Phase 1 is the only ship-blocker. Phases 2-3 are high priority but don't prevent usage of non-MM strategies. Phase 4 is polish.

---

## Files Touched (by phase)

**Phase 1:** `common/models.py`, `parent/hl_proxy.py`, `skills/wolf/scripts/standalone_runner.py`, `strategies/*.py` (6 MM + 3 directional)
**Phase 2:** `parent/hl_proxy.py`, `modules/movers_engine.py`, `cli/keystore.py`
**Phase 3:** `quoting_engine/` (TBD), `cli/commands/builder.py`, README.md
**Phase 4:** `scripts/bootstrap.sh`, `modules/wolf_engine.py`, `cli/commands/deposit.py` (new)
