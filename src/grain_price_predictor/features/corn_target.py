"""Construct the corn price target series from SNIIM data.

Target: MXN per ton, Sinaloa-origin white corn, monthly frequency.

Note: SNIIM reports wholesale (mayoreo) prices at destination markets.
The producer price at farm gate is typically ~10% lower.
Phase 1.5 will layer in the SEGALMEX policy-floor decomposition
(piso + market premium). For Phase 1 MVP we use the SNIIM price as
a direct proxy — it tracks the effective floor closely because of
SEGALMEX intervention.
"""
import numpy as np
import pandas as pd
from grain_price_predictor.utils.storage import load_raw

KG_TO_TON = 1000.0

# Approximate SEGALMEX/SADER policy floor prices (MXN/ton) by OI cycle year.
# Source: public SEGALMEX announcements; kept as reference for Phase 1.5.
POLICY_FLOOR_MXN_TON: dict[int, float] = {
    2020: 3_800,
    2021: 4_500,
    2022: 5_050,
    2023: 5_500,
    2024: 5_800,
    2025: 6_100,
}


def load_corn_target(origen: str = "Sinaloa") -> pd.Series:
    """Load SNIIM maíz blanco and return a monthly Series (MXN/ton).

    Averages across destination markets within each month.
    Returns a Series indexed by period-start timestamp (MS freq).
    """
    df = load_raw("sniim", "grano_maiz_blanco")
    if df is None or df.empty:
        raise FileNotFoundError(
            "SNIIM corn data not found — run: "
            "python scripts/download_all.py --sources sniim"
        )

    df = df.copy()
    df["fecha"] = pd.to_datetime(df["fecha"])

    # Filter origin state
    sinaloa = df[df["origen"].astype(str).str.strip() == origen].copy()
    if sinaloa.empty:
        raise ValueError(
            f"No rows with origen='{origen}'. "
            f"Available: {sorted(df['origen'].dropna().unique())}"
        )

    # Use promedio_mensual (monthly average); replace 0 / '--' with NaN
    sinaloa["promedio_mensual"] = pd.to_numeric(
        sinaloa["promedio_mensual"], errors="coerce"
    ).replace(0, np.nan)

    # One value per month — average across multiple destination-market rows
    monthly = (
        sinaloa.groupby("fecha")["promedio_mensual"]
        .mean()
        .dropna()
        .sort_index()
    )

    # Align to month-start timestamps
    monthly.index = pd.DatetimeIndex(monthly.index).to_period("M").to_timestamp("D").normalize()
    monthly = monthly * KG_TO_TON
    monthly.name = "precio_mxn_ton"
    return monthly


def add_policy_floor(series: pd.Series) -> pd.DataFrame:
    """Attach the approximate policy floor and compute the market premium.

    Returns a DataFrame with columns:
      precio_mxn_ton, policy_floor, market_premium
    """
    df = series.to_frame()
    df["policy_floor"] = df.index.year.map(POLICY_FLOOR_MXN_TON)
    df["market_premium"] = df["precio_mxn_ton"] - df["policy_floor"]
    return df
