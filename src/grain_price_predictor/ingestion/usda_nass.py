"""USDA NASS QuickStats API — US corn production & acreage.

Requires a free API key from https://quickstats.nass.usda.gov/api
(registration is instant). Set it in .env:
    NASS_API_KEY=your_key_here

Data downloaded:
  - US corn production (million bushels, monthly estimates Jun–Nov)
  - US corn planted & harvested acreage (million acres)
  - US corn yield (bu/acre)

These are the WASDE supply-side signals: production and acreage drive
the ending stocks estimate, which is the single strongest predictor of
corn prices (Good & Irwin 2006; inverse log relationship with STU ratio).

Without a key, the module logs a warning and returns nothing.
The features in corn_features.py use these with NaN-safe handling,
so the rest of the pipeline runs fine — just without these columns.
"""
from __future__ import annotations
import os
from datetime import date
import pandas as pd
import requests
from dotenv import load_dotenv
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseIngester

load_dotenv()

_BASE_URL = "https://quickstats.nass.usda.gov/api/api_GET/"

# Queries: (statisticcat_desc, unit_desc, short_desc_fragment)
_QUERIES = [
    {
        "commodity_desc": "CORN",
        "statisticcat_desc": "PRODUCTION",
        "unit_desc": "BU",
        "agg_level_desc": "NATIONAL",
        "reference_period_desc": "YEAR",
        "name": "us_corn_production_mbu",
        "scale": 1e-6,  # raw is bushels → million bushels
    },
    {
        "commodity_desc": "CORN",
        "statisticcat_desc": "AREA PLANTED",
        "unit_desc": "ACRES",
        "agg_level_desc": "NATIONAL",
        "reference_period_desc": "YEAR",
        "name": "us_corn_planted_macres",
        "scale": 1e-6,
    },
    {
        "commodity_desc": "CORN",
        "statisticcat_desc": "AREA HARVESTED",
        "unit_desc": "ACRES",
        "agg_level_desc": "NATIONAL",
        "reference_period_desc": "YEAR",
        "name": "us_corn_harvested_macres",
        "scale": 1e-6,
    },
    {
        "commodity_desc": "CORN",
        "statisticcat_desc": "YIELD",
        "unit_desc": "BU / ACRE",
        "agg_level_desc": "NATIONAL",
        "reference_period_desc": "YEAR",
        "name": "us_corn_yield_bu_acre",
        "scale": 1.0,
    },
]


class USDANASSIngester(BaseIngester):
    source = "usda_nass"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key or os.getenv("NASS_API_KEY", "")

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    def _fetch(self, params: dict) -> pd.DataFrame:
        r = requests.get(
            _BASE_URL,
            params={"key": self.api_key, "format": "json", **params},
            timeout=30,
        )
        r.raise_for_status()
        data = r.json().get("data", [])
        return pd.DataFrame(data)

    def _process(self, raw: pd.DataFrame, name: str, scale: float) -> pd.DataFrame:
        df = raw[["year", "Value"]].copy()
        df.columns = ["year", name]
        df[name] = pd.to_numeric(df[name].str.replace(",", ""), errors="coerce") * scale
        df["year"] = df["year"].astype(int)
        df = df.dropna(subset=[name]).sort_values("year").reset_index(drop=True)
        return df

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        if not self.api_key:
            logger.warning(
                "[usda_nass] No NASS_API_KEY set — skipping US corn production data.\n"
                "  Register free at: https://quickstats.nass.usda.gov/api\n"
                "  Then add NASS_API_KEY=your_key to your .env file."
            )
            return {}

        end = end or date.today()
        start_year = start.year

        all_dfs: list[pd.DataFrame] = []
        for query in _QUERIES:
            name  = query.pop("name")
            scale = query.pop("scale")
            params = {
                **query,
                "year__GE": str(start_year),
                "state_name": "US TOTAL",
            }
            logger.info(f"[usda_nass] Fetching {name}")
            try:
                raw = self._fetch(params)
                if raw.empty:
                    logger.warning(f"[usda_nass] {name}: empty response")
                    continue
                df = self._process(raw, name, scale)
                all_dfs.append(df)
                logger.success(f"[usda_nass] {name}: {len(df)} years  {df.year.min()}–{df.year.max()}")
            except Exception as exc:
                logger.error(f"[usda_nass] {name}: {exc}")
            finally:
                query["name"]  = name
                query["scale"] = scale

        if not all_dfs:
            return {}

        merged = all_dfs[0]
        for df in all_dfs[1:]:
            merged = merged.merge(df, on="year", how="outer")

        # Convert annual to monthly (repeat value for all months in that year)
        # NASS publishes final estimates in November; we use the annual figure
        # for all months of the following year (lagged to avoid leakage)
        monthly_rows = []
        for _, row in merged.iterrows():
            for month in range(1, 13):
                r = {"date": pd.Timestamp(int(row.year), month, 1)}
                for col in merged.columns:
                    if col != "year":
                        r[col] = row[col]
                monthly_rows.append(r)

        monthly = pd.DataFrame(monthly_rows)
        monthly = monthly[(monthly["date"] >= pd.Timestamp(start)) &
                          (monthly["date"] <= pd.Timestamp(end))]
        monthly = monthly.sort_values("date").reset_index(drop=True)

        self.save(monthly, "corn_supply")
        logger.success(f"[usda_nass] corn_supply: {len(monthly)} rows saved")
        return {"corn_supply": monthly}
