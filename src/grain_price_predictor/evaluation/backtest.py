"""Rolling-origin cross-validation and metrics.

All evaluation is strictly temporal — training always ends before the test
date, exactly as in production use.

Key function: evaluate_harvest_windows()
  Focuses on April predictions (the spec's primary anchor date), since
  that is when the producer's commercialization decision has the most value.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from loguru import logger


# ---------------------------------------------------------------------------
# Metric primitives
# ---------------------------------------------------------------------------

def _mae(a: np.ndarray, p: np.ndarray) -> float:
    return float(np.nanmean(np.abs(a - p)))

def _rmse(a: np.ndarray, p: np.ndarray) -> float:
    return float(np.sqrt(np.nanmean((a - p) ** 2)))

def _mape(a: np.ndarray, p: np.ndarray) -> float:
    mask = a != 0
    return float(np.nanmean(np.abs((a[mask] - p[mask]) / a[mask])) * 100)

def _pinball(a: np.ndarray, p: np.ndarray, q: float) -> float:
    err = a - p
    return float(np.nanmean(np.where(err >= 0, q * err, (q - 1) * err)))

def _dir_acc(a: np.ndarray, p: np.ndarray) -> float:
    if len(a) < 2:
        return float("nan")
    return float(np.mean(np.sign(np.diff(a)) == np.sign(np.diff(p))) * 100)

def _coverage(a: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> float:
    return float(np.nanmean((a >= lo) & (a <= hi)) * 100)


# ---------------------------------------------------------------------------
# Rolling-origin backtest
# ---------------------------------------------------------------------------

def rolling_origin_backtest(
    model_class,
    X: pd.DataFrame,
    y: pd.Series,
    min_train_months: int = 18,
    horizon: int = 1,
    feature_cols: list[str] | None = None,
) -> pd.DataFrame:
    """Train-through-t, predict t+horizon for every valid t.

    Returns DataFrame with columns: actual, p10, p50, p90 (or predicted).
    """
    fc = feature_cols or list(X.columns)
    Xf = X[fc].copy()

    # Drop rows missing any key feature
    valid_mask = Xf.notna().all(axis=1) & y.notna()
    Xf = Xf[valid_mask]
    yv = y[valid_mask]
    idx = Xf.index

    records: list[dict] = []
    for i in range(min_train_months, len(idx) - horizon + 1):
        train_end = idx[i - 1]
        test_start = idx[i]
        test_end   = idx[min(i + horizon - 1, len(idx) - 1)]

        X_tr = Xf.loc[:train_end]
        y_tr = yv.loc[:train_end]
        X_te = Xf.loc[test_start:test_end]

        try:
            m = model_class()
            m.fit(X_tr, y_tr)
            preds = m.predict(X_te)
        except Exception as exc:
            logger.warning(f"[backtest] {model_class.__name__} at {test_start}: {exc}")
            continue

        actuals = yv.loc[test_start:test_end]
        for d, a in zip(actuals.index, actuals.values):
            rec: dict = {"date": d, "actual": float(a)}
            if isinstance(preds, pd.DataFrame):
                for col in preds.columns:
                    rec[col] = float(preds.loc[d, col])
            else:
                rec["p50"] = float(preds.loc[d] if hasattr(preds, "loc") else preds)
            records.append(rec)

    df = pd.DataFrame(records)
    if df.empty:
        return df
    return df.set_index("date").sort_index()


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def compute_metrics(results: pd.DataFrame) -> pd.Series:
    a = results["actual"].values
    pred_col = "p50" if "p50" in results.columns else [c for c in results.columns if c != "actual"][0]
    p = results[pred_col].values

    out: dict = {
        "n":       len(a),
        "mae":     _mae(a, p),
        "rmse":    _rmse(a, p),
        "mape_%":  _mape(a, p),
        "dir_acc_%": _dir_acc(a, p),
    }
    if "p10" in results.columns and "p90" in results.columns:
        out["coverage_80%"] = _coverage(a, results["p10"].values, results["p90"].values)
        out["pinball_p10"] = _pinball(a, results["p10"].values, 0.10)
        out["pinball_p50"] = _pinball(a, results["p50"].values, 0.50)
        out["pinball_p90"] = _pinball(a, results["p90"].values, 0.90)

    return pd.Series(out)


# ---------------------------------------------------------------------------
# Interval calibration
# ---------------------------------------------------------------------------

def calibrate_interval_width(
    results: pd.DataFrame,
    target_coverage: float = 0.80,
    tol: float = 1e-4,
) -> float:
    """Find the scalar multiplier k that stretches [p10, p90] to hit target_coverage.

    Applies a symmetric multiplicative stretch around p50:
        lo = p50 - k * (p50 - p10)
        hi = p50 + k * (p90 - p50)

    Uses bisection over k in [0, 20]. Returns k=1.0 if intervals are absent.
    """
    if "p10" not in results.columns or "p90" not in results.columns:
        return 1.0

    a   = results["actual"].values
    p50 = results["p50"].values
    p10 = results["p10"].values
    p90 = results["p90"].values
    half_lo = p50 - p10   # raw half-width on lower side
    half_hi = p90 - p50   # raw half-width on upper side

    def coverage_at(k: float) -> float:
        return float(np.nanmean((a >= p50 - k * half_lo) & (a <= p50 + k * half_hi)))

    lo_k, hi_k = 0.0, 20.0
    for _ in range(64):
        mid = (lo_k + hi_k) / 2
        if coverage_at(mid) < target_coverage:
            lo_k = mid
        else:
            hi_k = mid
        if hi_k - lo_k < tol:
            break

    k = (lo_k + hi_k) / 2
    logger.info(f"[calibrate] k={k:.3f} → empirical coverage={coverage_at(k)*100:.1f}%")
    return k


def apply_calibration(results: pd.DataFrame, k: float) -> pd.DataFrame:
    """Return a copy of results with p10/p90 stretched by factor k around p50."""
    if k == 1.0 or "p10" not in results.columns:
        return results
    out = results.copy()
    out["p10"] = out["p50"] - k * (out["p50"] - out["p10"])
    out["p90"] = out["p50"] + k * (out["p90"] - out["p50"])
    return out


# ---------------------------------------------------------------------------
# Harvest-window evaluation (spec §7.3)
# ---------------------------------------------------------------------------

def evaluate_harvest_windows(
    results: pd.DataFrame,
    anchor_month: int = 4,
) -> pd.DataFrame:
    """Filter backtest results to the anchor month (April for corn).

    Returns one row per year showing actual vs predicted at the harvest anchor,
    which is the primary metric of business value per the spec.
    """
    harvest = results[results.index.month == anchor_month].copy()
    harvest["year"] = harvest.index.year
    if "p50" in harvest.columns:
        harvest["error"] = harvest["p50"] - harvest["actual"]
        harvest["error_pct"] = harvest["error"] / harvest["actual"] * 100
    return harvest.reset_index()


# ---------------------------------------------------------------------------
# Multi-model comparison
# ---------------------------------------------------------------------------

def evaluate_all_models(
    X: pd.DataFrame,
    y: pd.Series,
    models: dict,
    min_train_months: int = 18,
    feature_cols: list[str] | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Run backtest for each model; return (per-model DataFrames, summary table)."""
    all_results: dict[str, pd.DataFrame] = {}
    all_metrics: dict[str, pd.Series] = {}

    for name, model_class in models.items():
        logger.info(f"[backtest] Running {name}")
        res = rolling_origin_backtest(model_class, X, y, min_train_months, feature_cols=feature_cols)
        if res.empty:
            logger.warning(f"[backtest] {name}: no predictions generated")
            continue
        all_results[name] = res
        all_metrics[name] = compute_metrics(res)
        logger.success(f"[backtest] {name}: MAE={all_metrics[name]['mae']:.0f}  MAPE={all_metrics[name]['mape_%']:.1f}%")

    metrics_df = pd.DataFrame(all_metrics).T if all_metrics else pd.DataFrame()
    return all_results, metrics_df
