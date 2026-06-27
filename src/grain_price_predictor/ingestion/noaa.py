"""NOAA — Oceanic Niño Index (ENSO / ONI).

Source: https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt
Updated monthly.  ONI >= +0.5 → El Niño;  ONI <= -0.5 → La Niña.
ENSO modulates northwest Mexico rainfall and US winter frosts months in advance.
"""
from datetime import date
import io
import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseIngester

ONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/oni.ascii.txt"


class NOAAIngester(BaseIngester):
    source = "noaa"

    # Maps the 3-month season code to the middle month number
    _SEAS_TO_MONTH = {
        "DJF": 1, "JFM": 2, "FMA": 3, "MAM": 4,
        "AMJ": 5, "MJJ": 6, "JJA": 7, "JAS": 8,
        "ASO": 9, "SON": 10, "OND": 11, "NDJ": 12,
    }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _fetch_oni(self) -> pd.DataFrame:
        r = requests.get(ONI_URL, timeout=30)
        r.raise_for_status()
        df = pd.read_fwf(io.StringIO(r.text))
        df.columns = df.columns.str.strip().str.upper()
        # Current format: SEAS  YR  TOTAL  ANOM
        df = df.rename(columns={"YR": "year", "TOTAL": "sst_3month", "ANOM": "oni_anomaly"})
        df["month"] = df["SEAS"].map(self._SEAS_TO_MONTH)
        df = df.dropna(subset=["month"])
        df["month"] = df["month"].astype(int)
        df["date"] = pd.to_datetime(
            df["year"].astype(str) + "-" + df["month"].astype(str).str.zfill(2) + "-01"
        )
        df["enso_phase"] = pd.cut(
            df["oni_anomaly"],
            bins=[-99, -0.5, 0.5, 99],
            labels=["nina", "neutral", "nino"],
        )
        keep = [c for c in ["date", "year", "month", "sst_3month", "oni_anomaly", "enso_phase"] if c in df.columns]
        return df[keep].reset_index(drop=True)

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        end = end or date.today()
        logger.info("[noaa] Fetching ONI (ENSO) index")
        try:
            df = self._fetch_oni()
            df = df[
                (df.date >= pd.Timestamp(start)) & (df.date <= pd.Timestamp(end))
            ].reset_index(drop=True)
            self.save(df, "enso_oni")
            logger.success(
                f"[noaa] enso_oni: {len(df):,} months  "
                f"{df.date.min().date()} → {df.date.max().date()}"
            )
            return {"enso_oni": df}
        except Exception as exc:
            logger.error(f"[noaa] ONI fetch failed: {exc}")
            return {}
