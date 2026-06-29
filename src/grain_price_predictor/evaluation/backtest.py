"""Rolling-origin cross-validation and metrics.

All evaluation is strictly temporal — training always ends before the test
date, exactly as in production use.

Key functions:
  rolling_origin_backtest() — expanding or rolling window CV
  cqr_calibrate()           — Conformalized Quantile Regression (Romano et al. 2019)
  diebold_mariano()         — paired test of equal predictive accuracy (DM 1995)
  evaluate_harvest_windows() — April anchor evaluation (spec §7.3)
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

def _mase(a: np.ndarray, p: np.ndarray, period: int = 12) -> float:
    """Mean Absolute Scaled Error (Hyndman & Koehler 2006).

    Scale = MAE of the seasonal-naïve benchmark on the same test array.
    MASE < 1 means the model beats seasonal naïve; directly comparable
    across different price scales (useful when vegetables are added).
    """
    if len(a) <= period:
        return float("nan")
    scale = np.nanmean(np.abs(a[period:] - a[:-period]))
    return _mae(a, p) / scale if scale > 0 else float("nan")

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
    window: int | None = None,
) -> pd.DataFrame:
    """Train-through-t, predict t+1 for every valid t.

    Args:
        model_class:      Class with fit(X, y) / predict(X) interface.
        X:                Feature DataFrame (monthly, DatetimeIndex).
        y:                Target Series (same index).
        min_train_months: Minimum observations before first prediction.
        horizon:          Steps ahead. Only horizon=1 is supported; set to
                          anything else raises NotImplementedError.
        feature_cols:     Subset of X.columns to use (default: all).
        window:           If set, use a rolling training window of this many
                          months instead of an expanding window. Helps avoid
                          anchoring to stale price regimes.

    Returns:
        DataFrame indexed by date with columns: actual, p10, p50, p90 (or predicted).
    """
    if horizon != 1:
        raise NotImplementedError(
            "horizon > 1 produces overlapping test windows and is not yet supported. "
            "Use horizon=1 and call predict() iteratively for multi-step forecasts."
        )

    fc = feature_cols or list(X.columns)
    Xf = X[fc].copy()

    valid_mask = Xf.notna().all(axis=1) & y.notna()
    Xf = Xf[valid_mask]
    yv = y[valid_mask]
    idx = Xf.index

    records: list[dict] = []
    for i in range(min_train_months, len(idx)):
        train_end  = idx[i - 1]
        test_date  = idx[i]

        if window is not None:
            train_start = idx[max(0, i - window)]
            X_tr = Xf.loc[train_start:train_end]
            y_tr = yv.loc[train_start:train_end]
        else:
            X_tr = Xf.loc[:train_end]
            y_tr = yv.loc[:train_end]

        X_te = Xf.loc[[test_date]]

        try:
            m = model_class()
            m.fit(X_tr, y_tr)
            preds = m.predict(X_te)
        except Exception as exc:
            logger.warning(f"[backtest] {model_class.__name__} at {test_date}: {exc}")
            continue

        rec: dict = {"date": test_date, "actual": float(yv.loc[test_date])}
        if isinstance(preds, pd.DataFrame):
            for col in preds.columns:
                rec[col] = float(preds.loc[test_date, col])
        elif hasattr(preds, "loc"):
            rec["p50"] = float(preds.loc[test_date])
        else:
            raise TypeError(f"Unexpected prediction type: {type(preds)}")
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
        "n":         len(a),
        "mae":       _mae(a, p),
        "rmse":      _rmse(a, p),
        "mape_%":    _mape(a, p),
        "mase":      _mase(a, p, period=12),
        "dir_acc_%": _dir_acc(a, p),
    }
    if "p10" in results.columns and "p90" in results.columns:
        out["coverage_80%"] = _coverage(a, results["p10"].values, results["p90"].values)
        out["pinball_p10"]  = _pinball(a, results["p10"].values, 0.10)
        out["pinball_p50"]  = _pinball(a, results["p50"].values, 0.50)
        out["pinball_p90"]  = _pinball(a, results["p90"].values, 0.90)

    return pd.Series(out)


# ---------------------------------------------------------------------------
# Interval calibration — CQR (Romano, Patterson & Candès, NeurIPS 2019)
# ---------------------------------------------------------------------------

def cqr_calibrate(
    results: pd.DataFrame,
    calib_cutoff: str | pd.Timestamp,
    target_coverage: float = 0.80,
) -> tuple[float, pd.DataFrame]:
    """Conformalized Quantile Regression calibration.

    Temporally splits backtest results into:
      - calibration: [start, calib_cutoff)  used to compute additive correction
      - evaluation:  [calib_cutoff, end]    receives corrected intervals

    Nonconformity score: s_i = max(p10_i - y_i, y_i - p90_i)
      Positive when y falls outside the interval; negative when inside.
    The correction q̂ (in MXN/ton) is the (1-α) quantile of calibration scores,
    adjusted for finite sample: level = ⌈(n+1)(1-α)⌉ / n.

    Returns (q_hat, full_results_with_corrected_intervals).
    Unlike the global-k multiplier, CQR gives an additive correction that
    handles asymmetric errors and provides a finite-sample marginal coverage
    guarantee on the evaluation set.
    """
    if "p10" not in results.columns or "p90" not in results.columns:
        logger.warning("[cqr] No p10/p90 columns — skipping calibration")
        return 0.0, results

    cutoff = pd.Timestamp(calib_cutoff)
    cal  = results[results.index < cutoff]
    test = results[results.index >= cutoff]

    if len(cal) < 10:
        logger.warning(f"[cqr] Calibration set only {len(cal)} rows (< 10); skipping")
        return 0.0, results
    if test.empty:
        logger.warning("[cqr] Evaluation set is empty after cutoff; skipping")
        return 0.0, results

    scores = np.maximum(
        cal["p10"].values - cal["actual"].values,
        cal["actual"].values - cal["p90"].values,
    )

    n = len(cal)
    alpha = 1.0 - target_coverage
    level = min(np.ceil((n + 1) * (1 - alpha)) / n, 1.0)
    q_hat = float(np.quantile(scores, level))

    cal_cov  = float(np.mean(scores <= q_hat) * 100)
    test_cov = _coverage(
        test["actual"].values,
        test["p10"].values - q_hat,
        test["p90"].values + q_hat,
    )
    logger.info(
        f"[cqr] n_cal={n}  cutoff={calib_cutoff}  q̂={q_hat:.0f} MXN/ton  "
        f"cal_cov={cal_cov:.1f}%  oos_cov={test_cov:.1f}%"
    )

    corrected = results.copy()
    corrected["p10"] = corrected["p10"] - q_hat
    corrected["p90"] = corrected["p90"] + q_hat
    return q_hat, corrected


def apply_calibration(results: pd.DataFrame, k: float) -> pd.DataFrame:
    """Multiplicative stretch of p10/p90 around p50 by factor k.

    Legacy helper kept for comparison. Prefer cqr_calibrate() for production use.
    """
    if abs(k - 1.0) < 1e-6 or "p10" not in results.columns:
        return results
    out = results.copy()
    out["p10"] = out["p50"] - k * (out["p50"] - out["p10"])
    out["p90"] = out["p50"] + k * (out["p90"] - out["p50"])
    return out


# ---------------------------------------------------------------------------
# Diebold-Mariano test (Diebold & Mariano, JBES 1995)
# ---------------------------------------------------------------------------

def diebold_mariano(
    results_a: pd.DataFrame,
    results_b: pd.DataFrame,
    loss: str = "mae",
) -> dict:
    """Paired test of equal predictive accuracy between two models.

    H0: model_a and model_b have equal expected loss.
    Negative dm_stat means model_a has lower loss (i.e., is better).

    Args:
        results_a: backtest DataFrame for model A (must have 'actual', 'p50').
        results_b: backtest DataFrame for model B.
        loss:      'mae' or 'mse'.

    Returns dict with keys: dm_stat, p_value, n, mean_loss_diff.
      p_value < 0.05 means the difference is statistically significant.
    """
    common = results_a.index.intersection(results_b.index)
    if len(common) < 10:
        return {"dm_stat": float("nan"), "p_value": float("nan"),
                "n": len(common), "mean_loss_diff": float("nan")}

    p50_a = results_a.loc[common, "p50"].values
    p50_b = results_b.loc[common, "p50"].values
    actual = results_a.loc[common, "actual"].values

    if loss == "mae":
        d = np.abs(actual - p50_a) - np.abs(actual - p50_b)
    else:
        d = (actual - p50_a) ** 2 - (actual - p50_b) ** 2

    n = len(d)
    d_bar = float(np.mean(d))
    se = float(np.std(d, ddof=1) / np.sqrt(n))
    dm_stat = d_bar / se if se > 0 else float("nan")

    try:
        from scipy import stats
        p_value = float(2 * stats.t.sf(abs(dm_stat), df=n - 1))
    except ImportError:
        import math
        p_value = float(2 * (1 - 0.5 * math.erfc(-abs(dm_stat) / math.sqrt(2))))

    return {"dm_stat": dm_stat, "p_value": p_value, "n": n, "mean_loss_diff": d_bar}


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
        harvest["error"]     = harvest["p50"] - harvest["actual"]
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
    window: int | None = None,
) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    """Run backtest for each model; return (per-model DataFrames, summary table)."""
    all_results: dict[str, pd.DataFrame] = {}
    all_metrics: dict[str, pd.Series] = {}

    for name, model_class in models.items():
        logger.info(f"[backtest] Running {name}")
        res = rolling_origin_backtest(
            model_class, X, y, min_train_months,
            feature_cols=feature_cols, window=window,
        )
        if res.empty:
            logger.warning(f"[backtest] {name}: no predictions generated")
            continue
        all_results[name] = res
        all_metrics[name] = compute_metrics(res)
        logger.success(
            f"[backtest] {name}: MAE={all_metrics[name]['mae']:.0f}  "
            f"MAPE={all_metrics[name]['mape_%']:.1f}%  "
            f"MASE={all_metrics[name]['mase']:.2f}"
        )

    metrics_df = pd.DataFrame(all_metrics).T if all_metrics else pd.DataFrame()
    return all_results, metrics_df
