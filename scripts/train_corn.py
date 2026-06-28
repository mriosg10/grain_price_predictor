"""Phase 1 corn model training script.

Usage:
    python scripts/train_corn.py [--min-train 18] [--no-lgbm]

Steps:
  1. Load SNIIM corn target (MXN/ton, Sinaloa origin)
  2. Build feature matrix (AR lags, CME, FX, ENSO, calendar)
  3. Run rolling-origin backtest for all baselines + LightGBM
  4. Print metrics comparison table
  5. Save best model → models/corn_lgbm_v1.pkl
  6. Save backtest results → data/processed/corn_backtest_v1.parquet
  7. Print April (harvest-anchor) predictions
"""
from __future__ import annotations
import sys
from pathlib import Path

# Make sure package is importable when run from repo root
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import click
import pandas as pd
from loguru import logger
from rich.console import Console
from rich.table import Table

from grain_price_predictor.features.corn_target import load_corn_target
from grain_price_predictor.features.corn_features import build_corn_features, get_feature_cols
from grain_price_predictor.models.baselines import (
    NaiveSeasonalModel,
    SeasonalMeanModel,
    RandomWalkModel,
    TrendSeasonalModel,
)
from grain_price_predictor.models.corn_lgbm import CornLGBMModel
from grain_price_predictor.evaluation.backtest import (
    evaluate_all_models,
    evaluate_harvest_windows,
)

console = Console()


def _rich_metrics_table(metrics_df: pd.DataFrame) -> Table:
    tbl = Table(title="Rolling-Origin Backtest — All Models", show_lines=True)
    tbl.add_column("Model", style="bold cyan")
    tbl.add_column("N", justify="right")
    tbl.add_column("MAE", justify="right")
    tbl.add_column("RMSE", justify="right")
    tbl.add_column("MAPE %", justify="right")
    tbl.add_column("Dir Acc %", justify="right")
    tbl.add_column("Cov 80%", justify="right")

    for model_name, row in metrics_df.iterrows():
        tbl.add_row(
            str(model_name),
            f"{int(row['n'])}",
            f"{row['mae']:.0f}",
            f"{row['rmse']:.0f}",
            f"{row['mape_%']:.1f}",
            f"{row['dir_acc_%']:.1f}",
            f"{row.get('coverage_80%', float('nan')):.1f}" if "coverage_80%" in row else "—",
        )
    return tbl


def _rich_harvest_table(harvest_df: pd.DataFrame, model_name: str) -> Table:
    tbl = Table(title=f"April Harvest-Window Predictions — {model_name}", show_lines=True)
    tbl.add_column("Year", justify="right")
    tbl.add_column("Actual (MXN/ton)", justify="right")
    tbl.add_column("P50 (MXN/ton)", justify="right")
    tbl.add_column("Error", justify="right")
    tbl.add_column("Error %", justify="right")

    for _, r in harvest_df.iterrows():
        year = int(r.get("year", r.get("date", "?").year if hasattr(r.get("date"), "year") else "?"))
        actual = r["actual"]
        p50 = r.get("p50", float("nan"))
        err = r.get("error", float("nan"))
        err_pct = r.get("error_pct", float("nan"))
        color = "red" if abs(err_pct) > 10 else "green"
        tbl.add_row(
            str(year),
            f"{actual:,.0f}",
            f"{p50:,.0f}" if pd.notna(p50) else "—",
            f"[{color}]{err:+,.0f}[/{color}]" if pd.notna(err) else "—",
            f"[{color}]{err_pct:+.1f}%[/{color}]" if pd.notna(err_pct) else "—",
        )
    return tbl


@click.command()
@click.option("--min-train", default=18, show_default=True,
              help="Minimum months of training data before first prediction.")
@click.option("--no-lgbm", is_flag=True, default=False,
              help="Skip LightGBM (run baselines only — faster for debugging).")
@click.option("--save-model/--no-save-model", default=True, show_default=True)
def main(min_train: int, no_lgbm: bool, save_model: bool) -> None:
    # ── 1. Target ──────────────────────────────────────────────────────────
    console.rule("[bold]Step 1 — Load corn target")
    try:
        price = load_corn_target(origen="Sinaloa")
    except FileNotFoundError as exc:
        logger.error(str(exc))
        sys.exit(1)

    console.print(f"  Target rows: [cyan]{len(price)}[/cyan]  "
                  f"[{price.index.min().date()} → {price.index.max().date()}]")
    console.print(f"  Price range: {price.min():.0f} – {price.max():.0f} MXN/ton")

    # ── 2. Features ────────────────────────────────────────────────────────
    console.rule("[bold]Step 2 — Build feature matrix")
    feat = build_corn_features(price)
    feature_cols = get_feature_cols(feat)
    console.print(f"  Features: [cyan]{len(feature_cols)}[/cyan] columns")

    # Drop rows where price is missing (can't train without a target)
    valid = price.reindex(feat.index).dropna()
    feat  = feat.reindex(valid.index)
    console.print(f"  Valid rows (price + features): [cyan]{len(valid)}[/cyan]")

    # ── 3. Models ──────────────────────────────────────────────────────────
    console.rule("[bold]Step 3 — Rolling-origin backtest")
    models: dict = {
        "NaiveSeasonal":  NaiveSeasonalModel,
        "SeasonalMean":   SeasonalMeanModel,
        "RandomWalk":     RandomWalkModel,
        "TrendSeasonal":  TrendSeasonalModel,
    }
    if not no_lgbm:
        models["CornLGBM_P50"] = CornLGBMModel

    all_results, metrics_df = evaluate_all_models(
        feat, valid, models,
        min_train_months=min_train,
        feature_cols=feature_cols,
    )

    if metrics_df.empty:
        logger.error("No backtest results — check that you have enough data.")
        sys.exit(1)

    # ── 4. Metrics table ───────────────────────────────────────────────────
    console.rule("[bold]Step 4 — Metrics")
    console.print(_rich_metrics_table(metrics_df))

    # ── 5. Harvest-window table ────────────────────────────────────────────
    console.rule("[bold]Step 5 — April anchor predictions")
    lgbm_key = "CornLGBM_P50" if "CornLGBM_P50" in all_results else list(all_results.keys())[-1]
    harvest_df = evaluate_harvest_windows(all_results[lgbm_key], anchor_month=4)
    if harvest_df.empty:
        console.print("  [yellow]No April predictions in backtest window.[/yellow]")
    else:
        console.print(_rich_harvest_table(harvest_df, lgbm_key))

    # ── 6. Save results ────────────────────────────────────────────────────
    console.rule("[bold]Step 6 — Persist artifacts")
    out_dir = ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    for model_name, res_df in all_results.items():
        safe = model_name.lower().replace(" ", "_")
        res_df.to_parquet(out_dir / f"corn_backtest_{safe}.parquet")

    # Save combined metrics
    metrics_df.to_csv(out_dir / "corn_metrics_v1.csv")
    console.print(f"  Backtest results → [cyan]{out_dir}[/cyan]")
    console.print(f"  Metrics → [cyan]{out_dir / 'corn_metrics_v1.csv'}[/cyan]")

    if save_model and not no_lgbm and "CornLGBM_P50" in all_results:
        models_dir = ROOT / "models"
        models_dir.mkdir(exist_ok=True)
        # Re-train on full dataset to save production model
        console.print("  Re-training LightGBM on full data for production model...")
        prod_model = CornLGBMModel()
        prod_model.fit(feat[feature_cols].dropna(), valid.reindex(feat[feature_cols].dropna().index).dropna())
        prod_model.save(models_dir / "corn_lgbm_v1.pkl")
        console.print(f"  Model → [cyan]{models_dir / 'corn_lgbm_v1.pkl'}[/cyan]")

        # Feature importance
        console.rule("[bold]Feature Importance (P50 model, top 15)")
        fi = prod_model.feature_importance.head(15)
        fi_tbl = Table(show_lines=False)
        fi_tbl.add_column("Feature", style="cyan")
        fi_tbl.add_column("Importance", justify="right")
        for fname, imp in fi.items():
            fi_tbl.add_row(fname, f"{imp:.0f}")
        console.print(fi_tbl)

    console.rule("[bold green]Done")


if __name__ == "__main__":
    main()
