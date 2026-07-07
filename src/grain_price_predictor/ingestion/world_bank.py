"""World Bank Commodity Markets (Pink Sheet) ingestion.

Downloads the monthly Pink Sheet Excel file and extracts:
  - Maize (corn) global price in USD/mt
  - Wheat global price in USD/mt (useful as a substitute-crop signal)

The Pink Sheet reflects the same supply/demand conditions as USDA WASDE
because it IS derived from those estimates — global maize price moves
directly with US ending stocks. Unlike CME (US domestic), the WB price
also captures South American harvest cycles (Brazil/Argentina account for
~25% of global exports) and is quoted in USD/mt at a world-market reference.

No API key required. Updated monthly.
"""
from __future__ import annotations
from datetime import date
from io import BytesIO
import pandas as pd
import requests
from loguru import logger

from .base import BaseIngester

# World Bank hosts this at a stable path; the hash in the URL is their CMS key
_PINK_SHEET_URL = (
    "https://thedocs.worldbank.org/en/doc/"
    "5d903e848db1d1b83e0ec8f744e55570-0350012021/related/"
    "CMO-Historical-Data-Monthly.xlsx"
)

# Column names to extract (as they appear in the Pink Sheet header row)
_COMMODITIES = {
    "Maize":  "maize_usd_mt",
    "Wheat":  "wheat_usd_mt",
    "Soybeans": "soy_usd_mt",
}


class WorldBankIngester(BaseIngester):
    source = "world_bank"

    def _fetch_pink_sheet(self) -> pd.DataFrame:
        logger.info("[world_bank] Downloading Pink Sheet from World Bank…")
        r = requests.get(
            _PINK_SHEET_URL,
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=60,
        )
        r.raise_for_status()
        xl = pd.ExcelFile(BytesIO(r.content))
        raw = xl.parse("Monthly Prices", header=None)

        # Row 4 = commodity names, row 5 = units, data from row 6 onward
        header_row = raw.iloc[4]
        date_col   = raw.iloc[6:, 0]   # format: "1960M01"

        out = pd.DataFrame()
        out["date"] = pd.to_datetime(
            date_col.str.replace("M", "-", regex=False),
            format="%Y-%m",
        )

        for label, col_name in _COMMODITIES.items():
            col_idx = header_row[header_row == label].index
            if col_idx.empty:
                logger.warning(f"[world_bank] Column '{label}' not found in Pink Sheet")
                continue
            idx = col_idx[0]
            out[col_name] = pd.to_numeric(raw.iloc[6:, idx].values, errors="coerce")

        out = out.dropna(subset=["date"]).reset_index(drop=True)
        out["source"] = "world_bank_pink_sheet"
        return out

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        end = end or date.today()

        df = self._fetch_pink_sheet()
        df = df[(df["date"] >= pd.Timestamp(start)) & (df["date"] <= pd.Timestamp(end))]
        df = df.sort_values("date").reset_index(drop=True)

        self.save(df, "commodity_prices")
        logger.success(
            f"[world_bank] commodity_prices: {len(df):,} rows  "
            f"{df['date'].min().date()} → {df['date'].max().date()}"
        )
        return {"commodity_prices": df}
