"""Banxico SIE REST API — USD/MXN exchange rates.

Primary:  Banxico SIE API (requires a free token from https://www.banxico.org.mx/SieAPIRest/).
          Set BANXICO_TOKEN in your .env file.
Fallback: yfinance MXN=X ticker — no token needed, slightly lower precision.
"""
import os
from datetime import date
import pandas as pd
import requests
import yfinance as yf
from dotenv import load_dotenv
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseIngester

load_dotenv()

BASE_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1"

SERIES = {
    "usdmxn_fix": "SF43718",  # Tipo de cambio FIX — most common reference rate
    "usdmxn_48h": "SF60653",  # Tipo de cambio a 48 horas
}


class BanxicoIngester(BaseIngester):
    source = "banxico"

    def __init__(self, token: str | None = None):
        self.token = token or os.getenv("BANXICO_TOKEN", "")

    def _headers(self) -> dict:
        h = {"Accept": "application/json"}
        if self.token:
            h["Bmx-Token"] = self.token
        return h

    @retry(stop=stop_after_attempt(2), wait=wait_exponential(min=2, max=8))
    def _fetch_api(self, series_id: str, start: date, end: date) -> pd.DataFrame:
        url = f"{BASE_URL}/series/{series_id}/datos/{start.isoformat()}/{end.isoformat()}"
        r = requests.get(url, headers=self._headers(), timeout=30)
        r.raise_for_status()
        raw = r.json()["bmx"]["series"][0]["datos"]
        df = pd.DataFrame(raw, columns=["date", "value"])
        df["date"] = pd.to_datetime(df["date"], dayfirst=True)
        df["value"] = pd.to_numeric(df["value"].replace("N/E", pd.NA), errors="coerce")
        df["series_id"] = series_id
        df["source"] = "banxico_api"
        return df.dropna(subset=["value"]).reset_index(drop=True)

    def _fetch_yfinance(self, start: date, end: date) -> pd.DataFrame:
        """Fallback: Yahoo Finance MXN=X (USD per MXN, so we invert to get MXN/USD)."""
        logger.info("[banxico] Falling back to yfinance MXN=X")
        raw = yf.download(
            "MXN=X",
            start=start.isoformat(),
            end=end.isoformat(),
            auto_adjust=True,
            progress=False,
            multi_level_index=False,
        )
        if raw.empty:
            raise ValueError("yfinance returned empty dataframe for MXN=X")
        df = raw[["Close"]].reset_index()
        df.columns = ["date", "value"]
        df["date"] = pd.to_datetime(df["date"])
        # MXN=X is USD per 1 MXN; invert to get MXN per 1 USD
        df["value"] = 1.0 / df["value"]
        df["series_id"] = "MXN=X_inverted"
        df["source"] = "yfinance"
        return df.dropna(subset=["value"]).reset_index(drop=True)

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        end = end or date.today()
        results: dict[str, pd.DataFrame] = {}

        if self.token:
            for name, series_id in SERIES.items():
                logger.info(f"[banxico] {name} ({series_id})  {start} → {end}")
                try:
                    df = self._fetch_api(series_id, start, end)
                    self.save(df, name)
                    logger.success(
                        f"[banxico] {name}: {len(df):,} rows  "
                        f"{df.date.min().date()} → {df.date.max().date()}"
                    )
                    results[name] = df
                except Exception as exc:
                    logger.error(f"[banxico] API {name} failed: {exc}")
        else:
            logger.warning(
                "[banxico] No BANXICO_TOKEN set — using yfinance fallback. "
                "Register free at https://www.banxico.org.mx/SieAPIRest/ for official FIX rate."
            )

        # Always supplement with yfinance (covers gaps or runs standalone)
        if not results:
            try:
                df = self._fetch_yfinance(start, end)
                self.save(df, "usdmxn_yfinance")
                logger.success(
                    f"[banxico] usdmxn_yfinance: {len(df):,} rows  "
                    f"{df.date.min().date()} → {df.date.max().date()}"
                )
                results["usdmxn_yfinance"] = df
            except Exception as exc:
                logger.error(f"[banxico] yfinance fallback also failed: {exc}")

        return results
