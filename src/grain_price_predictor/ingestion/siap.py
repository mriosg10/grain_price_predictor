"""SIAP — Servicio de Información Agroalimentaria y Pesquera.

Downloads two datasets:
1. Cierre agrícola — annual production statistics by crop/state/cycle.
   Portal: https://nube.siap.gob.mx/cierre_agricola/
2. Avance de siembra y cosecha — weekly harvest progress during the season.
   Portal: https://nube.siap.gob.mx/avance_agricola/

Both portals are JavaScript-driven.  This module queries their internal
AJAX endpoints.  If those change, use load_from_file() to load a manually
exported Excel/CSV from the SIAP portal instead.

Sinaloa state code: 25
OI cycle code: 1  (Otoño-Invierno)
PV cycle code: 2  (Primavera-Verano)
"""
from __future__ import annotations
from datetime import date
from pathlib import Path
import pandas as pd
import requests
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseIngester

SINALOA = "25"

# SIAP crop codes for our scope
CROP_CODES: dict[str, str] = {
    "maiz_grano":   "1068",
    "tomate_rojo":  "1143",  # jitomate/tomate
    "chile_verde":  "1022",
    "pepino":       "1106",
    "berenjena":    "1013",
    "calabacita":   "1018",
    "ejote":        "1050",
}

# AJAX endpoint for cierre agrícola (reverse-engineered from portal)
CIERRE_URL = "https://nube.siap.gob.mx/cierreagricola/Cierre_Agricola.asmx/ObtenerCultivos"
AVANCE_URL = "https://nube.siap.gob.mx/avance_agricola/AvanceSiembraCosecha.asmx/ObtenerCultivos"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}


class SIAPIngester(BaseIngester):
    source = "siap"

    # ------------------------------------------------------------------
    # Cierre agrícola
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    def _fetch_cierre_year(
        self, year: int, crop_code: str, ciclo: str = "1", modalidad: str = "R"
    ) -> pd.DataFrame:
        payload = {
            "anio": str(year),
            "ciclo": ciclo,
            "modalidad": modalidad,
            "estado": SINALOA,
            "municipio": "0",
            "cultivo": crop_code,
            "tipo": "1",
        }
        r = requests.post(CIERRE_URL, json=payload, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "d" in data:
            data = data["d"]
        if not data:
            return pd.DataFrame()
        df = pd.DataFrame(data) if isinstance(data, list) else pd.json_normalize(data)
        df["year"] = year
        df["ciclo"] = ciclo
        df["modalidad"] = modalidad
        return df

    def download_cierre(
        self,
        start_year: int = 2010,
        end_year: int | None = None,
        ciclo: str = "1",
    ) -> dict[str, pd.DataFrame]:
        end_year = end_year or date.today().year
        results: dict[str, pd.DataFrame] = {}

        for crop_name, crop_code in CROP_CODES.items():
            frames: list[pd.DataFrame] = []
            for year in range(start_year, end_year + 1):
                try:
                    df = self._fetch_cierre_year(year, crop_code, ciclo)
                    if not df.empty:
                        frames.append(df)
                except Exception as exc:
                    logger.warning(f"[siap] cierre {crop_name} {year}: {exc}")

            if frames:
                combined = pd.concat(frames, ignore_index=True)
                combined["crop"] = crop_name
                key = f"cierre_{crop_name}"
                self.save(combined, key)
                logger.success(f"[siap] {key}: {len(combined):,} rows  {start_year}→{end_year}")
                results[key] = combined
            else:
                logger.warning(
                    f"[siap] No cierre data for {crop_name} — "
                    "if the AJAX endpoint has changed, export manually from "
                    "https://nube.siap.gob.mx/cierre_agricola/ and use load_from_file()"
                )

        return results

    # ------------------------------------------------------------------
    # Avance de siembra y cosecha (weekly)
    # ------------------------------------------------------------------

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    def _fetch_avance_cycle(
        self, anio_agricola: str, crop_code: str, ciclo: str = "1"
    ) -> pd.DataFrame:
        payload = {
            "anioAgricola": anio_agricola,
            "ciclo": ciclo,
            "modalidad": "R",
            "estado": SINALOA,
            "municipio": "0",
            "cultivo": crop_code,
        }
        r = requests.post(AVANCE_URL, json=payload, headers=HEADERS, timeout=30)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "d" in data:
            data = data["d"]
        if not data:
            return pd.DataFrame()
        return pd.DataFrame(data) if isinstance(data, list) else pd.json_normalize(data)

    def download_avance(
        self,
        start_year: int = 2015,
        end_year: int | None = None,
    ) -> dict[str, pd.DataFrame]:
        end_year = end_year or date.today().year
        results: dict[str, pd.DataFrame] = {}

        for crop_name, crop_code in CROP_CODES.items():
            frames: list[pd.DataFrame] = []
            for year in range(start_year, end_year + 1):
                anio_agricola = f"{year}/{year + 1}"
                try:
                    df = self._fetch_avance_cycle(anio_agricola, crop_code)
                    if not df.empty:
                        df["anio_agricola"] = anio_agricola
                        df["crop"] = crop_name
                        frames.append(df)
                except Exception as exc:
                    logger.warning(f"[siap] avance {crop_name} {anio_agricola}: {exc}")

            if frames:
                combined = pd.concat(frames, ignore_index=True)
                key = f"avance_{crop_name}"
                self.save(combined, key)
                logger.success(f"[siap] {key}: {len(combined):,} rows")
                results[key] = combined
            else:
                logger.warning(
                    f"[siap] No avance data for {crop_name} — "
                    "export manually from https://nube.siap.gob.mx/avance_agricola/ "
                    "and use load_from_file()"
                )

        return results

    # ------------------------------------------------------------------
    # Manual-download fallback
    # ------------------------------------------------------------------

    def load_from_file(self, path: str | Path, crop: str, dataset: str = "cierre") -> pd.DataFrame:
        """Load a manually exported SIAP Excel or CSV file.

        Args:
            path: Path to the downloaded file (.xlsx or .csv).
            crop: Crop name tag to attach (e.g. 'maiz_grano').
            dataset: 'cierre' or 'avance' — determines the save key.
        """
        p = Path(path)
        if p.suffix == ".csv":
            df = pd.read_csv(p, encoding="latin-1")
        else:
            df = pd.read_excel(p)
        df["crop"] = crop
        key = f"{dataset}_{crop}"
        self.save(df, key)
        logger.success(f"[siap] loaded {key} from {p.name}: {len(df):,} rows")
        return df

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        end = end or date.today()
        results = {}
        results.update(self.download_cierre(start.year, end.year))
        results.update(self.download_avance(start.year, end.year))
        return results
