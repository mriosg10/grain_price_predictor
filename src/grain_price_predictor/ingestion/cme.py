"""CME corn futures via yfinance.

ZC=F  — CBOT front-month continuous corn contract, quoted in USD/bushel.
Conversion stored in output: 1 bushel corn = 25.401 kg  →  USD/ton = USD/bu / 0.025401
"""
from datetime import date
import pandas as pd
import yfinance as yf
from loguru import logger

from .base import BaseIngester

BUSHEL_TO_TON = 25.401 / 1000  # 1 bushel = 25.401 kg


class CMEIngester(BaseIngester):
    source = "cme"

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        end = end or date.today()
        logger.info(f"[cme] Downloading ZC=F (corn continuous)  {start} → {end}")
        try:
            raw = yf.download(
                "ZC=F",
                start=start.isoformat(),
                end=end.isoformat(),
                auto_adjust=True,
                progress=False,
                multi_level_index=False,
            )
            if raw.empty:
                logger.warning("[cme] yfinance returned empty dataframe — futures data may be unavailable for this range")
                return {}

            df = raw[["Close", "Volume"]].copy().reset_index()
            df.columns = ["date", "close_usd_per_bushel", "volume"]
            df["date"] = pd.to_datetime(df["date"])
            df["close_usd_per_ton"] = df["close_usd_per_bushel"] / BUSHEL_TO_TON
            df = df.dropna(subset=["close_usd_per_bushel"]).reset_index(drop=True)

            self.save(df, "corn_zc_f")
            logger.success(
                f"[cme] corn_zc_f: {len(df):,} rows  "
                f"{df.date.min().date()} → {df.date.max().date()}"
            )
            return {"corn_zc_f": df}

        except Exception as exc:
            logger.error(f"[cme] download failed: {exc}")
            return {}
