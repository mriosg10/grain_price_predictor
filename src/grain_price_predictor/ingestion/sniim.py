"""SNIIM — Sistema Nacional de Información e Integración de Mercados.

Wholesale (mayoreo) price history for vegetables and grains in Mexico.
Source: http://www.economia-sniim.gob.mx/Nuevo/

The site uses ASP.NET WebForms; we capture ViewState on each GET then POST.
Prices are MXN per kg (ddlPrecios=2, calculated from commercial presentation).
"""
from __future__ import annotations
from datetime import date, timedelta
import time
import pandas as pd
import requests
from bs4 import BeautifulSoup, Tag
from loguru import logger
from tenacity import retry, stop_after_attempt, wait_exponential

from .base import BaseIngester

_BASE = "http://www.economia-sniim.gob.mx/Nuevo/Consultas/MercadosNacionales/PreciosDeMercado/Agricolas"

URLS = {
    "hortalizas": f"{_BASE}/ConsultaFrutasYHortalizas.aspx",
    "granos":     f"{_BASE}/ConsultaGranos.aspx",
}

# Product codes confirmed from live ddlProducto select (June 2026)
HORTALIZAS: dict[str, str] = {
    "tomate_bola":       "836",
    "tomate_saladette":  "839",
    "chile_jalapeno":    "233",
    "chile_serrano":     "246",
    "chile_morron":      "239",
    "pepino":            "771",
    "berenjena":         "157",
    "calabacita_criolla":"166",
    "calabacita_italiana":"170",
    "ejote":             "302",
}

GRANOS: dict[str, str] = {
    "maiz_blanco": "605",
}

# Key markets
ORIGEN_SINALOA = "25"   # Sinaloa origin
DESTINO_ALL    = "-1"   # All destination markets

CHUNK_DAYS = 90  # SNIIM times out on very long ranges

MONTH_NAMES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril",
    5: "Mayo", 6: "Junio", 7: "Julio", 8: "Agosto",
    9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}

# Columns returned by the price table
PRICE_COLS = ["fecha", "presentacion", "destino", "precio_min", "precio_max", "precio_frec", "obs"]


class SNIIMIngester(BaseIngester):
    source = "sniim"

    def __init__(self) -> None:
        self._session = requests.Session()
        self._session.headers.update(HEADERS)

    def _get_viewstate(self, url: str) -> dict:
        r = self._session.get(url, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        return {
            "__VIEWSTATE":          (soup.find("input", {"name": "__VIEWSTATE"}) or {}).get("value", ""),
            "__VIEWSTATEGENERATOR": (soup.find("input", {"name": "__VIEWSTATEGENERATOR"}) or {}).get("value", ""),
            "__EVENTVALIDATION":    (soup.find("input", {"name": "__EVENTVALIDATION"}) or {}).get("value", ""),
        }

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    def _query(
        self,
        url: str,
        product_code: str,
        start: date,
        end: date,
        origen: str = ORIGEN_SINALOA,
    ) -> pd.DataFrame:
        vs = self._get_viewstate(url)
        payload = {
            **vs,
            "ddlProducto":    product_code,
            "ddlOrigen":      origen,
            "ddlDestino":     DESTINO_ALL,
            "ddlPrecios":     "2",              # MXN/kg calculated
            "txtFechaInicio": start.strftime("%d/%m/%Y"),
            "txtFechaFinal":  end.strftime("%d/%m/%Y"),
            "btnBuscar.x":    "10",
            "btnBuscar.y":    "10",
        }
        r = self._session.post(url, data=payload, timeout=45)
        r.raise_for_status()
        r.encoding = "utf-8"

        soup = BeautifulSoup(r.text, "lxml")
        # Price table has Fecha/Destino/Precio columns
        for table in soup.find_all("table"):
            rows = table.find_all("tr")
            if len(rows) < 3:
                continue
            header = [td.get_text(strip=True) for td in rows[0].find_all(["td", "th"])]
            if not any(h in header for h in ["Fecha", "Precio", "Destino"]):
                continue
            data_rows = [
                [td.get_text(strip=True) for td in row.find_all(["td", "th"])]
                for row in rows[1:]
                if row.find_all(["td", "th"])
            ]
            # Filter out section label rows (single merged cell)
            data_rows = [r for r in data_rows if len(r) >= 5]
            if not data_rows:
                return pd.DataFrame()
            df = pd.DataFrame(data_rows, columns=PRICE_COLS[: len(data_rows[0])])
            df["fecha"] = pd.to_datetime(df["fecha"], dayfirst=True, errors="coerce")
            for col in ["precio_min", "precio_max", "precio_frec"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce")
            return df.dropna(subset=["fecha"]).reset_index(drop=True)

        return pd.DataFrame()

    def _fetch_product(
        self, url: str, product_name: str, product_code: str, start: date, end: date
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        chunk_start = start
        while chunk_start <= end:
            chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS), end)
            try:
                df = self._query(url, product_code, chunk_start, chunk_end)
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                logger.warning(f"[sniim] {product_name} {chunk_start}→{chunk_end}: {exc}")
            chunk_start = chunk_end + timedelta(days=1)
            time.sleep(1.5)

        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=3, max=15))
    def _query_grano_month(self, product_code: str, month: int, year: int) -> pd.DataFrame:
        import io
        url = URLS["granos"]
        vs = self._get_viewstate(url)
        payload = {
            **vs,
            "ddlProducto":        product_code,
            "ddlOrigen":          "-1",            # all origins
            "ddlDestino":         DESTINO_ALL,
            "ddlPrecios":         "2",
            "ddlDestinoMensual":  "-1",
            "ddlMesMensual":      MONTH_NAMES[month],
            "ddlAnioMensual":     str(year),
            "btnBuscarMensual.x": "10",
            "btnBuscarMensual.y": "10",
        }
        r = self._session.post(url, data=payload, timeout=45)
        r.raise_for_status()
        r.encoding = "utf-8"

        all_tables = pd.read_html(io.StringIO(r.text), encoding="utf-8")
        # The data table has 8 columns and > 100 rows
        data_tables = [t for t in all_tables if t.shape[1] == 8 and t.shape[0] > 10]
        if not data_tables:
            return pd.DataFrame()

        raw = data_tables[0].copy()
        # Row 1 contains true column names; row 0 is merged header
        raw.columns = raw.iloc[1].tolist()
        raw = raw.iloc[2:].reset_index(drop=True)
        raw.columns = ["producto", "origen", "sem1", "sem2", "sem3", "sem4", "sem5", "promedio_mensual"]

        # Forward-fill product name (NaN rows belong to previous product)
        raw["producto"] = raw["producto"].ffill()

        # Keep only target product rows
        target_name = "Maíz blanco" if product_code == "605" else "Maíz blanco pozolero"
        df = raw[raw["producto"].astype(str).str.strip() == target_name].copy()
        if df.empty:
            return pd.DataFrame()

        for col in ["sem1", "sem2", "sem3", "sem4", "sem5", "promedio_mensual"]:
            df[col] = pd.to_numeric(df[col].replace("--", pd.NA), errors="coerce")

        df["year"] = year
        df["month"] = month
        df["fecha"] = pd.to_datetime(f"{year}-{month:02d}-01")
        return df.reset_index(drop=True)

    def _fetch_grano_monthly(
        self, product_name: str, product_code: str, start: date, end: date
    ) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        yr, mo = start.year, start.month
        end_yr, end_mo = end.year, end.month
        while (yr, mo) <= (end_yr, end_mo):
            try:
                df = self._query_grano_month(product_code, mo, yr)
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                logger.warning(f"[sniim] {product_name} {yr}-{mo:02d}: {exc}")
            mo += 1
            if mo > 12:
                mo = 1
                yr += 1
            time.sleep(1.5)
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        end = end or date.today()
        results: dict[str, pd.DataFrame] = {}

        for name, code in HORTALIZAS.items():
            logger.info(f"[sniim] {name} (code={code})  {start} → {end}")
            df = self._fetch_product(URLS["hortalizas"], name, code, start, end)
            if not df.empty:
                df["product"] = name
                self.save(df, f"hortaliza_{name}")
                logger.success(f"[sniim] {name}: {len(df):,} rows")
                results[f"hortaliza_{name}"] = df
            else:
                logger.warning(f"[sniim] {name}: no data returned")

        for name, code in GRANOS.items():
            logger.info(f"[sniim] {name} (code={code})  {start} → {end}  [monthly query]")
            df = self._fetch_grano_monthly(name, code, start, end)
            if not df.empty:
                df["product"] = name
                self.save(df, f"grano_{name}")
                logger.success(f"[sniim] {name}: {len(df):,} rows")
                results[f"grano_{name}"] = df
            else:
                logger.warning(f"[sniim] {name}: no data returned")

        return results
