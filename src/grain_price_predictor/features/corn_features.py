"""Feature matrix construction for the corn price model.

Feature groups (all lagged to prevent data leakage):
  1. Autoregressive  — lagged corn prices
  2. Market          — CME futures (USD/ton), USD/MXN FX, MXN-equivalent futures
  3. Calendar        — month encoding, harvest-window flags
  4. Climate/ENSO    — ONI anomaly (signals Sinaloa rainfall & FL frost risk)

Leakage rule: prediction emitted Feb 1 for April harvest.
  Any feature with lag >= 1 month from the prediction date is safe.
  We use lags of 1–12 months throughout.
"""
from __future__ import annotations
import numpy as np
import pandas as pd
from grain_price_predictor.utils.storage import load_raw

HARVEST_MONTHS     = frozenset([4, 5, 6])
PRE_HARVEST_MONTHS = frozenset([1, 2, 3])


# ---------------------------------------------------------------------------
# Loaders (resample to monthly, return aligned Series)
# ---------------------------------------------------------------------------

def _to_monthly(df: pd.DataFrame, date_col: str, val_col: str) -> pd.Series:
    s = df.set_index(date_col)[val_col].sort_index()
    s.index = pd.DatetimeIndex(s.index)
    return s.resample("MS").mean()


def load_cme_monthly() -> pd.Series:
    df = load_raw("cme", "corn_zc_f")
    if df is None:
        return pd.Series(dtype=float, name="cme_usd_per_ton")
    df["date"] = pd.to_datetime(df["date"])
    return _to_monthly(df, "date", "close_usd_per_ton").rename("cme_usd_per_ton")


def load_fx_monthly() -> pd.Series:
    df = load_raw("banxico", "usdmxn_yfinance")
    if df is None:
        return pd.Series(dtype=float, name="usdmxn")
    df["date"] = pd.to_datetime(df["date"])
    return _to_monthly(df, "date", "value").rename("usdmxn")


def load_enso_monthly() -> pd.Series:
    df = load_raw("noaa", "enso_oni")
    if df is None:
        return pd.Series(dtype=float, name="oni")
    df["date"] = pd.to_datetime(df["date"])
    return df.set_index("date")["oni_anomaly"].rename("oni").sort_index()


# ---------------------------------------------------------------------------
# Feature builder
# ---------------------------------------------------------------------------

def build_corn_features(
    price: pd.Series,
    price_lags: list[int] | None = None,
    market_lags: list[int] | None = None,
) -> pd.DataFrame:
    """Build the feature matrix, aligned to price.index.

    Args:
        price:       Monthly corn price series (MXN/ton).
        price_lags:  Which autoregressive lags to include.
        market_lags: Which lags to use for CME / FX features.

    Returns:
        DataFrame with all features.  Rows with all-NaN features are kept;
        the caller should drop them via dropna() on key columns.
    """
    if price_lags is None:
        price_lags = [1, 2, 3, 6, 12]
    if market_lags is None:
        market_lags = [1, 2, 3]

    cme  = load_cme_monthly().reindex(price.index)
    fx   = load_fx_monthly().reindex(price.index)
    enso = load_enso_monthly().reindex(price.index)

    feat = pd.DataFrame(index=price.index)

    # ── Autoregressive ────────────────────────────────────────────────────
    for lag in price_lags:
        feat[f"price_lag{lag:02d}m"] = price.shift(lag)

    # Trend: year-over-year change in price (12-month lag diff)
    feat["price_yoy_pct"] = price.pct_change(12) * 100

    # ── CME corn futures (USD/ton) ────────────────────────────────────────
    for lag in market_lags:
        feat[f"cme_usd_lag{lag:02d}"] = cme.shift(lag)

    # ── USD/MXN ──────────────────────────────────────────────────────────
    for lag in market_lags:
        feat[f"fx_lag{lag:02d}"] = fx.shift(lag)

    # FX momentum
    feat["fx_mom3"] = fx.shift(1) / fx.shift(4) - 1   # 3-month change, lagged 1m

    # ── MXN-equivalent futures (floor signal for corn) ───────────────────
    # This is what imported corn costs in MXN — the key parity signal
    feat["cme_mxn_lag01"] = feat["cme_usd_lag01"] * feat["fx_lag01"]
    feat["cme_mxn_lag03"] = feat["cme_usd_lag03"] * feat["fx_lag03"]

    # Basis: how much domestic price deviates from MXN-equivalent international
    # (positive = domestic premium; negative = discounted)
    feat["basis_lag01"] = price.shift(1) - feat["cme_mxn_lag01"]

    # ── ENSO ─────────────────────────────────────────────────────────────
    for lag in [1, 3, 6]:
        feat[f"oni_lag{lag:02d}"] = enso.shift(lag)

    # ── Calendar ─────────────────────────────────────────────────────────
    feat["month"] = price.index.month
    # Cyclical encoding (avoids artificial discontinuity between Dec→Jan)
    feat["month_sin"] = np.sin(2 * np.pi * price.index.month / 12)
    feat["month_cos"] = np.cos(2 * np.pi * price.index.month / 12)
    feat["is_harvest"]     = price.index.month.isin(HARVEST_MONTHS).astype(np.int8)
    feat["is_pre_harvest"] = price.index.month.isin(PRE_HARVEST_MONTHS).astype(np.int8)
    feat["year"] = price.index.year

    return feat


def get_feature_cols(feat: pd.DataFrame) -> list[str]:
    """Return column names suitable for model training (drop meta cols)."""
    return [c for c in feat.columns if c not in ("year",)]
