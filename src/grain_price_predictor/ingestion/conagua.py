"""CONAGUA / SINA — Reservoir storage levels for Sinaloa.

The OI cycle is ~94% irrigated; dam storage in September-October is the
single strongest supply-side signal for both corn and vegetable acreage.

Portal: https://sina.conagua.gob.mx/sina/tema.php?tema=presasVolumen
API (SINAV): https://sinav30.conagua.gob.mx:8080/SINA/

If the API is unreachable, export the Excel from the SINA portal and use
load_from_file().
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseIngester

# Key Sinaloa dams with CONAGUA clave (ID).
# IDs confirmed from SINA portal; verify at sinav30.conagua.gob.mx if changed.
SINALOA_DAMS: dict[str, str] = {
    "adolfo_lopez_mateos":    "SIN004",  # El Fuerte — serves Ahome / Los Mochis
    "alvaro_obregon":         "SIN006",  # Río Sinaloa — serves Guasave
    "josefa_ortiz_dominguez": "SIN005",  # Río Sinaloa tributary
    "gustavo_diaz_ordaz":     "SIN002",  # Bacurato — Guasave / Angostura
    "sanalona":               "SIN001",  # Río Humaya — Culiacán valley
    "luis_donaldo_colosio":   "SIN003",  # El Varejonal — Culiacán valley
}

SINAV_BASE = "https://sinav30.conagua.gob.mx:8080/SINA"
SINA_CSV_URL = (
    "https://sina.conagua.gob.mx/sina/descarga.php"
    "?tema=presasVolumen&tipo=csv&estado=25"
)

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


class CONAGUAIngester(BaseIngester):
    source = "conagua"

    # ------------------------------------------------------------------
    # SINAV REST API (primary)
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    def _fetch_dam_sinav(self, clave: str, start: date, end: date) -> pd.DataFrame:
        url = (
            f"{SINAV_BASE}/almacenamiento/presa/{clave}"
            f"?fechaInicio={start.isoformat()}&fechaFin={end.isoformat()}"
        )
        r = requests.get(url, headers=HEADERS, timeout=30, verify=False)
        r.raise_for_status()
        data = r.json()
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data) if isinstance(data, list) else pd.json_normalize(data)
        df["clave"] = clave
        return df

    def _download_sinav(self, start: date, end: date) -> dict[str, pd.DataFrame]:
        results: dict[str, pd.DataFrame] = {}
        for name, clave in SINALOA_DAMS.items():
            logger.info(f"[conagua] {name} ({clave})  {start} → {end}")
            try:
                df = self._fetch_dam_sinav(clave, start, end)
                if not df.empty:
                    df["dam_name"] = name
                    self.save(df, f"dam_{name}")
                    logger.success(f"[conagua] {name}: {len(df):,} rows")
                    results[f"dam_{name}"] = df
                else:
                    logger.warning(f"[conagua] {name}: empty response")
            except Exception as exc:
                logger.warning(f"[conagua] SINAV failed for {name}: {exc}")
        return results

    # ------------------------------------------------------------------
    # SINA bulk CSV download (fallback)
    # ------------------------------------------------------------------

    def _download_sina_csv(self) -> pd.DataFrame:
        logger.info("[conagua] Trying SINA bulk CSV download for Sinaloa dams")
        r = requests.get(SINA_CSV_URL, headers={"User-Agent": "Mozilla/5.0"}, timeout=60)
        r.raise_for_status()
        df = pd.read_csv(
            __import__("io").StringIO(r.content.decode("latin-1")),
            low_memory=False,
        )
        return df

    # ------------------------------------------------------------------
    # Manual-download fallback
    # ------------------------------------------------------------------

    def load_from_file(self, path: str | Path) -> pd.DataFrame:
        """Load a manually exported SINA CSV/Excel.

        Export from: https://sina.conagua.gob.mx/sina/tema.php?tema=presasVolumen
        Select: Estado = Sinaloa, download as CSV/Excel.
        """
        p = Path(path)
        df = pd.read_csv(p, encoding="latin-1") if p.suffix == ".csv" else pd.read_excel(p)
        self.save(df, "reservoirs_sinaloa")
        logger.success(f"[conagua] loaded from {p.name}: {len(df):,} rows")
        return df

    # ------------------------------------------------------------------
    # Public download
    # ------------------------------------------------------------------

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        end = end or date.today()
        results = self._download_sinav(start, end)

        if not results:
            logger.warning(
                "[conagua] SINAV API returned no data. "
                "Trying SINA bulk CSV..."
            )
            try:
                df = self._download_sina_csv()
                if not df.empty:
                    self.save(df, "reservoirs_sinaloa_bulk")
                    results["reservoirs_sinaloa_bulk"] = df
                    logger.success(f"[conagua] bulk CSV: {len(df):,} rows")
            except Exception as exc:
                logger.error(
                    f"[conagua] bulk CSV also failed: {exc}\n"
                    "→ Export manually from https://sina.conagua.gob.mx/sina/ "
                    "and call load_from_file(path)"
                )

        return results
