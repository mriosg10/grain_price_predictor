"""Make a corn price forecast using the trained model.

Usage:
    python scripts/predict_corn.py                      # predict next April
    python scripts/predict_corn.py --date 2027-04-01   # predict a specific month
    python scripts/predict_corn.py --refresh            # re-download data first

The prediction is emitted as of today's latest data. It represents the
expected Sinaloa white-corn wholesale price (MXN/ton) on the target date.

Workflow reminder:
  1. Run this script monthly (or before any commercialization decision).
  2. The P50 is the point forecast; [P10, P90] is the 80% confidence interval.
  3. If today's price is near or below P10, that signals a possible buying
     opportunity vs. waiting for the harvest window.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
from datetime import date

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import click
import pandas as pd
import numpy as np
from loguru import logger
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from grain_price_predictor.features.corn_target import load_corn_target, POLICY_FLOOR_MXN_TON
from grain_price_predictor.features.corn_features import build_corn_features, get_feature_cols
from grain_price_predictor.models.corn_lgbm import CornLGBMModel

console = Console()

MODEL_PATH = ROOT / "models" / "corn_lgbm_v1.pkl"
CALIB_PATH = ROOT / "models" / "corn_lgbm_v1_calib.json"


def _next_april() -> str:
    today = date.today()
    year  = today.year if today.month < 4 else today.year + 1
    return f"{year}-04-01"


def _load_calibration() -> float:
    if not CALIB_PATH.exists():
        return 0.0
    cfg = json.loads(CALIB_PATH.read_text())
    return float(cfg.get("q_hat_mxn_ton", 0.0))


def _decision_guidance(p10: float, p50: float, p90: float, floor: float) -> str:
    width_pct = (p90 - p10) / p50 * 100
    if floor > 0 and p10 < floor * 1.05:
        return (
            f"[yellow]Caution:[/yellow] P10 ({p10:,.0f}) is near or below the "
            f"SEGALMEX floor ({floor:,.0f}). Policy support provides a floor, but "
            f"market premium is at risk."
        )
    if width_pct > 40:
        return (
            f"[yellow]High uncertainty[/yellow]: interval width is {width_pct:.0f}% of P50. "
            f"Consider waiting for more price signals closer to the harvest date."
        )
    if p50 > floor * 1.30 and floor > 0:
        return (
            f"[green]Positive outlook:[/green] P50 is {(p50/floor - 1)*100:.0f}% above "
            f"the policy floor. Market premium is healthy."
        )
    return f"[cyan]Normal range:[/cyan] forecast within historical norms."


@click.command()
@click.option("--date", "target_date", default=None,
              help="Target month to forecast (YYYY-MM-DD). Default: next April.")
@click.option("--refresh", is_flag=True, default=False,
              help="Re-download CME, FX, and ENSO data before predicting.")
def main(target_date: str | None, refresh: bool) -> None:

    if target_date is None:
        target_date = _next_april()

    target_ts = pd.Timestamp(target_date).replace(day=1)   # snap to month-start

    # ── Optionally refresh raw data ────────────────────────────────────────
    if refresh:
        console.print("[bold]Refreshing market data…[/bold]")
        import subprocess
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "download_all.py"),
             "--sources", "banxico,cme,noaa"],
            capture_output=True, text=True
        )
        if result.returncode != 0:
            console.print(f"[yellow]Data refresh warning:[/yellow] {result.stderr[-300:]}")
        else:
            console.print("  [green]Data refreshed.[/green]")

    # ── Load model ─────────────────────────────────────────────────────────
    if not MODEL_PATH.exists():
        console.print(
            f"[red]Model not found at {MODEL_PATH}[/red]\n"
            "Run: [bold]python scripts/train_corn.py[/bold] first."
        )
        sys.exit(1)

    model   = CornLGBMModel.load(MODEL_PATH)
    q_hat   = _load_calibration()
    feat_cols = model.feature_names_

    # ── Build features as of latest available data ─────────────────────────
    try:
        price = load_corn_target(origen="Sinaloa")
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    feat = build_corn_features(price)

    # We want to predict for target_ts. The feature row to use is the row
    # whose date is one month before target_ts (the most recent known data).
    as_of = target_ts - pd.DateOffset(months=1)
    as_of = as_of.replace(day=1)

    # Find the closest available row at or before as_of
    available = feat.index[feat.index <= as_of]
    if available.empty:
        console.print(f"[red]No feature data available on or before {as_of.date()}[/red]")
        sys.exit(1)

    row_date = available[-1]
    X_row = feat.loc[[row_date], feat_cols]

    # Check for missing features
    missing = X_row.columns[X_row.isna().any()].tolist()
    if missing:
        console.print(
            f"[yellow]Warning:[/yellow] {len(missing)} features are NaN "
            f"({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''}). "
            f"Prediction may be less reliable."
        )

    # ── Predict ────────────────────────────────────────────────────────────
    preds = model.predict(X_row)
    p10_raw = float(preds.loc[row_date, "p10"])
    p50     = float(preds.loc[row_date, "p50"])
    p90_raw = float(preds.loc[row_date, "p90"])

    # Apply CQR additive correction
    p10 = p10_raw - q_hat
    p90 = p90_raw + q_hat

    # Policy floor for target year
    floor = float(POLICY_FLOOR_MXN_TON.get(target_ts.year, 0))

    # ── Latest known price (context) ───────────────────────────────────────
    latest_price = float(price.iloc[-1])
    latest_date  = price.index[-1].date()

    # ── Output ─────────────────────────────────────────────────────────────
    console.print()
    console.print(Panel(
        f"[bold]Corn Price Forecast[/bold]  ·  Target: [cyan]{target_ts.strftime('%B %Y')}[/cyan]  "
        f"·  Features as of: [cyan]{row_date.date()}[/cyan]",
        expand=False,
    ))

    tbl = Table(show_header=True, show_lines=False, box=None, padding=(0, 2))
    tbl.add_column("", style="dim")
    tbl.add_column("MXN / ton", justify="right", style="bold")
    tbl.add_column("Context", style="dim")

    tbl.add_row("P10  (pessimistic)",  f"{p10:>10,.0f}", "80% interval lower bound")
    tbl.add_row("P50  (point forecast)", f"[green]{p50:>10,.0f}[/green]", "best estimate")
    tbl.add_row("P90  (optimistic)",   f"{p90:>10,.0f}", "80% interval upper bound")
    tbl.add_row("", "", "")
    tbl.add_row("SEGALMEX floor",
                f"{floor:>10,.0f}" if floor else "       n/a",
                f"{target_ts.year} policy floor")
    tbl.add_row("Latest known price",  f"{latest_price:>10,.0f}", f"({latest_date})")
    tbl.add_row("CQR correction (q̂)", f"{q_hat:>10,.0f}", "additive interval widening")

    console.print(tbl)
    console.print()
    console.print(_decision_guidance(p10, p50, p90, floor))
    console.print()

    # Warn about 2024-type anomaly risk
    if target_ts.year >= 2024 and p50 < floor * 1.10 and floor > 0:
        console.print(
            "[yellow]Note:[/yellow] In 2024, market prices spiked to 2.7× the policy floor "
            "due to a policy anomaly. The model cannot predict such structural breaks — "
            "monitor SEGALMEX announcements closely."
        )


if __name__ == "__main__":
    main()
