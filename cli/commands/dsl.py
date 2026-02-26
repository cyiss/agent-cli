"""hl dsl — Dynamic Stop Loss trailing stop commands."""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import typer

dsl_app = typer.Typer(
    name="dsl",
    help="Dynamic Stop Loss — trailing stop system for Hyperliquid perps.",
    no_args_is_help=True,
)


@dsl_app.command("start")
def dsl_start(
    instrument: str = typer.Argument(
        ..., help="Trading instrument (e.g., ETH-PERP, VXX-USDYP)",
    ),
    entry_price: float = typer.Option(
        ..., "--entry", "-e", help="Position entry price",
    ),
    size: float = typer.Option(
        ..., "--size", "-s", help="Position size (base units)",
    ),
    direction: str = typer.Option(
        "long", "--direction", "-d", help="Position direction: long or short",
    ),
    leverage: float = typer.Option(
        10.0, "--leverage", "-l", help="Position leverage",
    ),
    preset: Optional[str] = typer.Option(
        None, "--preset", "-p", help="Preset: moderate, tight",
    ),
    config: Optional[Path] = typer.Option(
        None, "--config", "-c", help="DSL YAML config file",
    ),
    tick: float = typer.Option(
        5.0, "--tick", "-t", help="Check interval in seconds",
    ),
    mainnet: bool = typer.Option(
        False, "--mainnet", help="Use mainnet (default: testnet)",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Log close orders but don't execute",
    ),
    mock: bool = typer.Option(
        False, "--mock", help="Use mock market data (no HL connection)",
    ),
    data_dir: str = typer.Option(
        "data/dsl", "--data-dir", help="Directory for DSL state files",
    ),
):
    """Start a standalone DSL guard for an existing position."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-14s %(levelname)-5s %(message)s",
        datefmt="%H:%M:%S",
    )

    from modules.dsl_config import DSLConfig, PRESETS
    from modules.dsl_guard import DSLGuard
    from modules.dsl_state import DSLState, DSLStateStore

    # Build config
    if config:
        dsl_cfg = DSLConfig.from_yaml(str(config))
    elif preset:
        if preset not in PRESETS:
            typer.echo(f"Unknown preset '{preset}'. Available: {', '.join(PRESETS.keys())}")
            raise typer.Exit(1)
        dsl_cfg = DSLConfig.from_dict(PRESETS[preset].to_dict())  # Copy
    else:
        dsl_cfg = DSLConfig()

    dsl_cfg.direction = direction
    dsl_cfg.leverage = leverage

    # Auto-compute absolute floor if not set (3% max loss)
    if dsl_cfg.phase1_absolute_floor == 0.0:
        if direction == "long":
            dsl_cfg.phase1_absolute_floor = entry_price * (1 - 0.03 / leverage)
        else:
            dsl_cfg.phase1_absolute_floor = entry_price * (1 + 0.03 / leverage)

    # Build state
    store = DSLStateStore(data_dir=data_dir)
    state = DSLState.new(
        instrument=instrument,
        entry_price=entry_price,
        position_size=size,
        direction=direction,
    )

    guard = DSLGuard(config=dsl_cfg, state=state, store=store)

    # Build HL adapter
    if mock or dry_run:
        from cli.hl_adapter import DirectMockProxy
        hl = DirectMockProxy()
        typer.echo(f"Mode: {'DRY RUN' if dry_run else 'MOCK'}")
    else:
        from cli.hl_adapter import DirectHLProxy
        from parent.hl_proxy import HLProxy
        import os

        key = os.environ.get("HL_PRIVATE_KEY", "")
        if not key:
            typer.echo("Error: HL_PRIVATE_KEY not set")
            raise typer.Exit(1)
        hl = DirectHLProxy(HLProxy(private_key=key, testnet=not mainnet))
        typer.echo(f"Mode: LIVE ({'mainnet' if mainnet else 'testnet'})")

    from cli.strategy_registry import resolve_instrument
    resolved = resolve_instrument(instrument)

    typer.echo(f"Instrument: {resolved}")
    typer.echo(f"Direction: {direction} | Entry: {entry_price} | Size: {size} | Leverage: {leverage}x")
    typer.echo(f"Preset: {preset or 'custom'} | Tiers: {len(dsl_cfg.tiers)}")
    typer.echo(f"Tick: {tick}s | State: {data_dir}/{state.position_id}.json")
    typer.echo("")

    from skills.dsl.scripts.standalone_runner import StandaloneDSLRunner

    runner = StandaloneDSLRunner(
        hl=hl,
        guard=guard,
        instrument=resolved,
        tick_interval=tick,
        dry_run=dry_run,
    )
    runner.run()


@dsl_app.command("status")
def dsl_status(
    data_dir: str = typer.Option(
        "data/dsl", "--data-dir", help="DSL state directory",
    ),
):
    """Show DSL state for all active guards."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.dsl_state import DSLStateStore

    store = DSLStateStore(data_dir=data_dir)
    active = store.list_active()

    if not active:
        typer.echo("No active DSL guards.")
        return

    typer.echo(f"{'ID':<30} {'Inst':<12} {'Dir':<6} {'Entry':>10} {'Tier':>5} {'ROE%':>8} {'Breaches':>9}")
    typer.echo("-" * 85)

    for pid in sorted(active):
        data = store.load(pid)
        if not data:
            continue
        s = data["state"]
        tier = s.get("current_tier_index", -1)
        tier_str = f"P1" if tier < 0 else f"T{tier}"
        typer.echo(
            f"{pid:<30} {s.get('instrument', '?'):<12} {s.get('direction', '?'):<6} "
            f"{s.get('entry_price', 0):>10.2f} {tier_str:>5} "
            f"{s.get('current_roe', 0):>7.1f}% {s.get('breach_count', 0):>9}"
        )


@dsl_app.command("presets")
def dsl_presets():
    """List available DSL presets with tier details."""
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    from modules.dsl_config import PRESETS

    for name, cfg in PRESETS.items():
        typer.echo(f"\n{name.upper()}")
        typer.echo(f"  Phase 1: retrace={cfg.phase1_retrace*100:.1f}%, breaches={cfg.phase1_max_breaches}")
        typer.echo(f"  Phase 2: retrace={cfg.phase2_retrace*100:.1f}%, breaches={cfg.phase2_max_breaches}")
        typer.echo(f"  Decay: {cfg.breach_decay_mode}")
        if cfg.stagnation_enabled:
            typer.echo(f"  Stagnation TP: ROE>={cfg.stagnation_min_roe}%, timeout={cfg.stagnation_timeout_ms/1000:.0f}s")

        typer.echo(f"  Tiers:")
        for i, t in enumerate(cfg.tiers):
            extras = []
            if t.retrace is not None:
                extras.append(f"retrace={t.retrace*100:.1f}%")
            if t.max_breaches is not None:
                extras.append(f"breaches={t.max_breaches}")
            extra_str = f" ({', '.join(extras)})" if extras else ""
            typer.echo(f"    {i}: trigger={t.trigger_pct:.0f}% -> lock={t.lock_pct:.0f}%{extra_str}")
