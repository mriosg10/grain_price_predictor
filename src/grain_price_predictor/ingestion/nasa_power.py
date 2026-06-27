"""NASA POWER API — daily climate data for Sinaloa and Florida (competitor region).

API docs: https://power.larc.nasa.gov/docs/
Rate limit: ~30 req/min without auth; we batch by year and sleep between calls.
NASA POWER uses -999 as fill value; we replace with NaN.
"""
from __future__ import annotations
import time
from datetime import date
import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseIngester

BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

PARAMETERS = "T2M_MAX,T2M_MIN,T2M,PRECTOTCORR,RH2M"

# Lat/lon for key production zones (Sinaloa) and competitor reference (Florida)
LOCATIONS: dict[str, tuple[float, float]] = {
    "culiacan":          (24.7994, -107.3938),
    "navolato":          (24.7676, -107.7023),
    "guasave":           (25.5637, -108.4631),
    "ahome":             (25.9237, -109.1802),
    "culiacan_south":    (24.1500, -107.0000),  # south Sinaloa (chiles)
    "florida_immokalee": (26.4183,  -81.4073),  # Florida winter-veggie competitor
}

PARAM_RENAME = {
    "T2M_MAX":      "temp_max_c",
    "T2M_MIN":      "temp_min_c",
    "T2M":          "temp_mean_c",
    "PRECTOTCORR":  "precip_mm",
    "RH2M":         "humidity_pct",
}


class NASAPowerIngester(BaseIngester):
    source = "nasa_power"

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=5, max=30))
    def _fetch_year(self, lat: float, lon: float, year: int) -> pd.DataFrame:
        r = requests.get(
            BASE_URL,
            params={
                "start": f"{year}0101",
                "end": f"{year}1231",
                "latitude": lat,
                "longitude": lon,
                "community": "AG",
                "parameters": PARAMETERS,
                "format": "JSON",
                "user": "grainmx",
                "header": "true",
            },
            timeout=60,
        )
        r.raise_for_status()
        data = r.json()["properties"]["parameter"]
        date_keys = list(next(iter(data.values())).keys())
        df = pd.DataFrame({"date": pd.to_datetime(date_keys, format="%Y%m%d")})
        for param, col in PARAM_RENAME.items():
            if param in data:
                df[col] = [data[param].get(d, float("nan")) for d in date_keys]
                df[col] = df[col].replace(-999.0, float("nan"))
        return df.reset_index(drop=True)

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
        locations: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        end = end or date.today()
        targets = {k: v for k, v in LOCATIONS.items() if locations is None or k in locations}
        results: dict[str, pd.DataFrame] = {}

        for name, (lat, lon) in targets.items():
            logger.info(f"[nasa_power] {name} ({lat}, {lon})  {start.year}→{end.year}")
            frames: list[pd.DataFrame] = []

            for year in range(start.year, end.year + 1):
                try:
                    frames.append(self._fetch_year(lat, lon, year))
                    time.sleep(2)  # respect rate limit
                except Exception as exc:
                    logger.warning(f"[nasa_power] {name} year {year} failed: {exc}")

            if not frames:
                logger.error(f"[nasa_power] no data for {name}")
                continue

            df = pd.concat(frames, ignore_index=True)
            df = df[
                (df.date >= pd.Timestamp(start)) & (df.date <= pd.Timestamp(end))
            ].reset_index(drop=True)
            df["location"] = name
            df["lat"] = lat
            df["lon"] = lon

            key = f"climate_{name}"
            self.save(df, key)
            logger.success(
                f"[nasa_power] {name}: {len(df):,} rows  "
                f"{df.date.min().date()} → {df.date.max().date()}"
            )
            results[key] = df

        return results
