"""Feature matrix construction for the corn price model.

Feature groups (all lagged to prevent data leakage):
  1. Autoregressive  — lagged corn prices
  2. Market          — CME futures (USD/ton), USD/MXN FX, MXN-equivalent futures
  3. Policy          — SEGALMEX floor (public information, known before planting)
  4. Calendar        — cyclical month encoding, harvest-window flags
  5. Climate/ENSO    — ONI anomaly + pre-harvest interaction

Leakage rule: prediction emitted Feb 1 for April harvest.
  Any feature with lag >= 1 month from the prediction date is safe.
  We use lags of 1–12 months throughout.
"""
from __future__ import annotations
import functools
import numpy as np
import pandas as pd
from grain_price_predictor.utils.storage import load_raw
from grain_price_predictor.features.corn_target import POLICY_FLOOR_MXN_TON

HARVEST_MONTHS     = frozenset([4, 5, 6])
PRE_HARVEST_MONTHS = frozenset([1, 2, 3])


# ---------------------------------------------------------------------------
# Loaders — cached so backtest doesn't re-read parquet on every fold
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def load_cme_monthly() -> pd.Series:
    df = load_raw("cme", "corn_zc_f")
    if df is None:
        return pd.Series(dtype=float, name="cme_usd_per_ton")
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")["close_usd_per_ton"].sort_index()
    return s.resample("MS").mean().rename("cme_usd_per_ton")


@functools.lru_cache(maxsize=1)
def load_fx_monthly() -> pd.Series:
    df = load_raw("banxico", "usdmxn_yfinance")
    if df is None:
        return pd.Series(dtype=float, name="usdmxn")
    df["date"] = pd.to_datetime(df["date"])
    s = df.set_index("date")["value"].sort_index()
    return s.resample("MS").mean().rename("usdmxn")


@functools.lru_cache(maxsize=1)
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
        DataFrame with all features. Rows with all-NaN features are kept;
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

    # YoY trend: lagged one extra month to avoid leaking t's value
    feat["price_yoy_pct"] = price.pct_change(12).shift(1) * 100

    # ── CME corn futures (USD/ton) ────────────────────────────────────────
    for lag in market_lags:
        feat[f"cme_usd_lag{lag:02d}"] = cme.shift(lag)

    # ── USD/MXN ──────────────────────────────────────────────────────────
    for lag in market_lags:
        feat[f"fx_lag{lag:02d}"] = fx.shift(lag)

    # FX momentum (3-month change, lagged 1m)
    feat["fx_mom3"] = fx.shift(1) / fx.shift(4) - 1

    # ── MXN-equivalent futures (parity floor signal) ──────────────────────
    for lag in market_lags:
        feat[f"cme_mxn_lag{lag:02d}"] = feat[f"cme_usd_lag{lag:02d}"] * feat[f"fx_lag{lag:02d}"]

    # Basis: domestic price vs. MXN-equivalent international (1-month lag)
    feat["basis_lag01"] = price.shift(1) - feat["cme_mxn_lag01"]

    # ── SEGALMEX policy floor ─────────────────────────────────────────────
    # Known before planting; explicit feature so the model doesn't have to
    # infer it from price levels alone (critical for 2024 anomaly).
    # Pre-SEGALMEX years (before 2020) have no published floor → fill with 0.
    feat["policy_floor"] = (
        price.index.year.map(POLICY_FLOOR_MXN_TON).astype(float).fillna(0.0)
    )
    feat["is_segalmex"] = (feat["policy_floor"] > 0).astype(np.int8)
    # Premium above floor at last known price (t-1)
    feat["floor_premium_lag01"] = price.shift(1) - feat["policy_floor"]

    # ── ENSO ─────────────────────────────────────────────────────────────
    for lag in [1, 3, 6]:
        feat[f"oni_lag{lag:02d}"] = enso.shift(lag)

    # ── Calendar ─────────────────────────────────────────────────────────
    # Cyclical encoding avoids the artificial Dec→Jan discontinuity.
    # month integer excluded from feature_cols (redundant + discontinuous).
    feat["month_sin"] = np.sin(2 * np.pi * price.index.month / 12)
    feat["month_cos"] = np.cos(2 * np.pi * price.index.month / 12)
    feat["is_harvest"]     = price.index.month.isin(HARVEST_MONTHS).astype(np.int8)
    feat["is_pre_harvest"] = price.index.month.isin(PRE_HARVEST_MONTHS).astype(np.int8)

    # ENSO × pre-harvest window interaction (Sinaloa winter rainfall signal)
    feat["oni_x_preharvest"] = feat["oni_lag03"] * feat["is_pre_harvest"]

    # meta columns (excluded from training)
    feat["month"] = price.index.month
    feat["year"]  = price.index.year

    return feat


def get_feature_cols(feat: pd.DataFrame) -> list[str]:
    """Return column names suitable for model training (drop meta cols)."""
    return [c for c in feat.columns if c not in ("year", "month")]
