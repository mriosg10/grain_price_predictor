#!/usr/bin/env python3
"""Phase 0 data download — run all (or selected) ingestion sources.

Usage:
    python scripts/download_all.py
    python scripts/download_all.py --sources banxico,cme,noaa
    python scripts/download_all.py --start 2015-01-01 --end 2024-12-31
    python scripts/download_all.py --sources nasa_power --locations culiacan,guasave
"""
from __future__ import annotations
import sys
from pathlib import Path

# Works whether or not the package is pip-installed
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from datetime import date
import click
from dotenv import load_dotenv
from loguru import logger

load_dotenv()

ALL_SOURCES = ["banxico", "cme", "noaa", "nasa_power", "sniim", "siap", "conagua"]


@click.command()
@click.option(
    "--sources",
    default=",".join(ALL_SOURCES),
    show_default=True,
    help="Comma-separated list of sources to run.",
)
@click.option("--start", default="2010-01-01", show_default=True, help="Start date YYYY-MM-DD.")
@click.option("--end", default=None, help="End date YYYY-MM-DD (default: today).")
@click.option(
    "--locations",
    default=None,
    help="NASA POWER only: comma-separated location names.",
)
def main(sources: str, start: str, end: str | None, locations: str | None) -> None:
    from grain_price_predictor.ingestion import (
        BanxicoIngester,
        CMEIngester,
        CONAGUAIngester,
        NASAPowerIngester,
        NOAAIngester,
        SIAPIngester,
        SNIIMIngester,
    )

    start_dt = date.fromisoformat(start)
    end_dt = date.fromisoformat(end) if end else date.today()
    selected = [s.strip() for s in sources.split(",")]
    location_list = [l.strip() for l in locations.split(",")] if locations else None

    logger.info(f"Download plan: {selected}  {start_dt} → {end_dt}")

    summary: dict[str, str] = {}

    if "banxico" in selected:
        results = BanxicoIngester().download(start_dt, end_dt)
        summary["banxico"] = f"{sum(len(v) for v in results.values()):,} rows across {len(results)} series"

    if "cme" in selected:
        results = CMEIngester().download(start_dt, end_dt)
        summary["cme"] = f"{sum(len(v) for v in results.values()):,} rows"

    if "noaa" in selected:
        results = NOAAIngester().download(start_dt, end_dt)
        summary["noaa"] = f"{sum(len(v) for v in results.values()):,} rows"

    if "nasa_power" in selected:
        results = NASAPowerIngester().download(start_dt, end_dt, location_list)
        summary["nasa_power"] = f"{sum(len(v) for v in results.values()):,} rows across {len(results)} locations"

    if "sniim" in selected:
        results = SNIIMIngester().download(start_dt, end_dt)
        if results:
            summary["sniim"] = f"{sum(len(v) for v in results.values()):,} rows across {len(results)} products"
        else:
            summary["sniim"] = "0 rows — check logs; may need probe_form() to debug field names"

    if "siap" in selected:
        results = SIAPIngester().download(start_dt, end_dt)
        if results:
            summary["siap"] = f"{sum(len(v) for v in results.values()):,} rows across {len(results)} datasets"
        else:
            summary["siap"] = "0 rows — AJAX endpoint may have changed; try load_from_file()"

    if "conagua" in selected:
        results = CONAGUAIngester().download(start_dt, end_dt)
        if results:
            summary["conagua"] = f"{sum(len(v) for v in results.values()):,} rows"
        else:
            summary["conagua"] = "0 rows — try load_from_file() with manual SINA export"

    click.echo("\n── Download Summary ─────────────────────────────")
    for src, msg in summary.items():
        click.echo(f"  {src:<12} {msg}")
    click.echo("─────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
