"""hl — Autonomous Hyperliquid trading CLI built on Tee-work strategies."""
from __future__ import annotations

import sys
from pathlib import Path

import typer

# Ensure project root is importable
_root = str(Path(__file__).resolve().parent.parent)
if _root not in sys.path:
    sys.path.insert(0, _root)

app = typer.Typer(
    name="hl",
    help="Autonomous Hyperliquid trader — direct HL API execution with TEE strategies.",
    no_args_is_help=True,
    add_completion=False,
)

from cli.commands.run import run_cmd
from cli.commands.status import status_cmd
from cli.commands.trade import trade_cmd
from cli.commands.account import account_cmd
from cli.commands.strategies import strategies_cmd
from cli.commands.dsl import dsl_app

app.command("run", help="Start autonomous trading with a strategy")(run_cmd)
app.command("status", help="Show positions, PnL, and risk state")(status_cmd)
app.command("trade", help="Place a single manual order")(trade_cmd)
app.command("account", help="Show HL account state")(account_cmd)
app.command("strategies", help="List available strategies")(strategies_cmd)
app.add_typer(dsl_app, name="dsl", help="Dynamic Stop Loss trailing stop system")


def main():
    app()


if __name__ == "__main__":
    main()
