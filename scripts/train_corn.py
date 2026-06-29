"""Phase 1 corn model training script.

Usage:
    python scripts/train_corn.py [--min-train 18] [--no-lgbm] [--window N]

Steps:
  1. Load SNIIM corn target (MXN/ton, Sinaloa origin)
  2. Build feature matrix (AR lags, CME, FX, ENSO, policy floor, calendar)
  3. Run rolling-origin backtest for all baselines + LightGBM
  4. CQR interval calibration (Romano, Patterson & Candès 2019)
  5. Diebold-Mariano test: LightGBM vs RandomWalk
  6. Print metrics comparison table (incl. MASE)
  7. Save best model → models/corn_lgbm_v1.pkl
  8. Save backtest results → data/processed/
  9. Print April (harvest-anchor) predictions
"""
from __future__ import annotations
import json
import sys
from pathlib import Path

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
    cqr_calibrate,
    compute_metrics,
    diebold_mariano,
)

console = Console()
CQR_CUTOFF = "2022-01-01"   # calibration: 2012–2021 (~10 yrs); OOS: 2022+


def _rich_metrics_table(metrics_df: pd.DataFrame) -> Table:
    tbl = Table(title="Rolling-Origin Backtest — All Models", show_lines=True)
    tbl.add_column("Model", style="bold cyan")
    tbl.add_column("N", justify="right")
    tbl.add_column("MAE", justify="right")
    tbl.add_column("RMSE", justify="right")
    tbl.add_column("MAPE %", justify="right")
    tbl.add_column("MASE", justify="right")
    tbl.add_column("Dir Acc %", justify="right")
    tbl.add_column("Cov 80%", justify="right")

    for model_name, row in metrics_df.iterrows():
        mase = row.get("mase", float("nan"))
        mase_str = f"{mase:.2f}" if pd.notna(mase) else "—"
        cov = row.get("coverage_80%", float("nan"))
        cov_str = f"{cov:.1f}" if pd.notna(cov) else "—"
        tbl.add_row(
            str(model_name),
            f"{int(row['n'])}",
            f"{row['mae']:.0f}",
            f"{row['rmse']:.0f}",
            f"{row['mape_%']:.1f}",
            mase_str,
            f"{row['dir_acc_%']:.1f}",
            cov_str,
        )
    return tbl


def _rich_harvest_table(harvest_df: pd.DataFrame, model_name: str) -> Table:
    tbl = Table(title=f"April Harvest-Window Predictions — {model_name}", show_lines=True)
    tbl.add_column("Year", justify="right")
    tbl.add_column("Actual (MXN/ton)", justify="right")
    tbl.add_column("P10", justify="right")
    tbl.add_column("P50 (MXN/ton)", justify="right")
    tbl.add_column("P90", justify="right")
    tbl.add_column("Error", justify="right")
    tbl.add_column("Error %", justify="right")

    has_intervals = "p10" in harvest_df.columns and "p90" in harvest_df.columns
    for _, r in harvest_df.iterrows():
        year    = int(r["year"])
        actual  = r["actual"]
        p50     = r.get("p50", float("nan"))
        err     = r.get("error", float("nan"))
        err_pct = r.get("error_pct", float("nan"))
        color   = "red" if pd.notna(err_pct) and abs(err_pct) > 10 else "green"
        tbl.add_row(
            str(year),
            f"{actual:,.0f}",
            f"{r['p10']:,.0f}" if has_intervals and pd.notna(r.get("p10")) else "—",
            f"{p50:,.0f}" if pd.notna(p50) else "—",
            f"{r['p90']:,.0f}" if has_intervals and pd.notna(r.get("p90")) else "—",
            f"[{color}]{err:+,.0f}[/{color}]" if pd.notna(err) else "—",
            f"[{color}]{err_pct:+.1f}%[/{color}]" if pd.notna(err_pct) else "—",
        )
    return tbl


@click.command()
@click.option("--min-train", default=18, show_default=True,
              help="Minimum months of training data before first prediction.")
@click.option("--no-lgbm", is_flag=True, default=False,
              help="Skip LightGBM (run baselines only).")
@click.option("--window", default=None, type=int,
              help="Rolling training window in months (default: expanding).")
@click.option("--save-model/--no-save-model", default=True, show_default=True)
def main(min_train: int, no_lgbm: bool, window: int | None, save_model: bool) -> None:

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

    valid = price.reindex(feat.index).dropna()
    feat  = feat.reindex(valid.index)
    console.print(f"  Valid rows (price + features): [cyan]{len(valid)}[/cyan]")
    if window:
        console.print(f"  Training window: [cyan]{window}[/cyan] months (rolling)")

    # ── 3. Backtest ────────────────────────────────────────────────────────
    console.rule("[bold]Step 3 — Rolling-origin backtest")
    models: dict = {
        "NaiveSeasonal": NaiveSeasonalModel,
        "SeasonalMean":  SeasonalMeanModel,
        "RandomWalk":    RandomWalkModel,
        "TrendSeasonal": TrendSeasonalModel,
    }
    if not no_lgbm:
        models["CornLGBM"] = CornLGBMModel

    all_results, metrics_df = evaluate_all_models(
        feat, valid, models,
        min_train_months=min_train,
        feature_cols=feature_cols,
        window=window,
    )

    if metrics_df.empty:
        logger.error("No backtest results — check that you have enough data.")
        sys.exit(1)

    # ── 4. CQR interval calibration ────────────────────────────────────────
    lgbm_key = "CornLGBM" if "CornLGBM" in all_results else None
    q_hat = 0.0
    if lgbm_key:
        console.rule("[bold]Step 4 — CQR interval calibration")
        console.print(f"  Calibration set: start → {CQR_CUTOFF}  |  "
                      f"OOS evaluation: {CQR_CUTOFF} → end")
        q_hat, calibrated = cqr_calibrate(
            all_results[lgbm_key], calib_cutoff=CQR_CUTOFF, target_coverage=0.80
        )
        all_results[lgbm_key] = calibrated
        oos_metrics = compute_metrics(calibrated[calibrated.index >= CQR_CUTOFF])
        metrics_df.loc[lgbm_key] = compute_metrics(calibrated)
        console.print(f"  Additive correction q̂ = [cyan]{q_hat:.0f}[/cyan] MXN/ton")
        console.print(f"  OOS 80% coverage ({CQR_CUTOFF}+): "
                      f"[green]{oos_metrics.get('coverage_80%', float('nan')):.1f}%[/green]")

    # ── 5. Diebold-Mariano test ────────────────────────────────────────────
    if lgbm_key and "RandomWalk" in all_results:
        console.rule("[bold]Step 5 — Diebold-Mariano test (LightGBM vs RandomWalk)")
        dm = diebold_mariano(all_results[lgbm_key], all_results["RandomWalk"], loss="mae")
        sig = "✓ significant" if dm["p_value"] < 0.05 else "✗ not significant at p<0.05"
        console.print(
            f"  DM stat = [cyan]{dm['dm_stat']:.2f}[/cyan]  "
            f"p = [cyan]{dm['p_value']:.3f}[/cyan]  "
            f"n = {dm['n']}  [{sig}]"
        )
        console.print(
            f"  Mean MAE advantage = [cyan]{-dm['mean_loss_diff']:.0f}[/cyan] MXN/ton "
            f"(negative dm_stat = LightGBM is better)"
        )

    # ── 6. Metrics table ───────────────────────────────────────────────────
    console.rule("[bold]Step 6 — Metrics")
    console.print(_rich_metrics_table(metrics_df))

    # ── 7. April harvest-window table ──────────────────────────────────────
    console.rule("[bold]Step 7 — April anchor predictions")
    show_key = lgbm_key or list(all_results.keys())[-1]
    harvest_df = evaluate_harvest_windows(all_results[show_key], anchor_month=4)
    if harvest_df.empty:
        console.print("  [yellow]No April predictions in backtest window.[/yellow]")
    else:
        console.print(_rich_harvest_table(harvest_df, show_key))

    # ── 8. Persist artifacts ───────────────────────────────────────────────
    console.rule("[bold]Step 8 — Persist artifacts")
    out_dir = ROOT / "data" / "processed"
    out_dir.mkdir(parents=True, exist_ok=True)

    for model_name, res_df in all_results.items():
        safe = model_name.lower().replace(" ", "_")
        res_df.to_parquet(out_dir / f"corn_backtest_{safe}.parquet")

    metrics_df.to_csv(out_dir / "corn_metrics_v1.csv")
    console.print(f"  Backtest results → [cyan]{out_dir}[/cyan]")
    console.print(f"  Metrics → [cyan]{out_dir / 'corn_metrics_v1.csv'}[/cyan]")

    if save_model and not no_lgbm and lgbm_key:
        models_dir = ROOT / "models"
        models_dir.mkdir(exist_ok=True)
        console.print("  Re-training LightGBM on full data for production model...")
        clean_idx = feat[feature_cols].dropna().index.intersection(valid.index)
        prod_model = CornLGBMModel()
        prod_model.fit(feat.loc[clean_idx, feature_cols], valid.loc[clean_idx])
        prod_model.save(models_dir / "corn_lgbm_v1.pkl")

        calib_path = models_dir / "corn_lgbm_v1_calib.json"
        calib_path.write_text(json.dumps({
            "method": "cqr",
            "q_hat_mxn_ton": q_hat,
            "calib_cutoff": CQR_CUTOFF,
            "target_coverage": 0.80,
        }, indent=2))
        console.print(f"  Model → [cyan]{models_dir / 'corn_lgbm_v1.pkl'}[/cyan]")
        console.print(f"  Calibration → [cyan]{calib_path}[/cyan]  (q̂={q_hat:.0f} MXN/ton)")

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
