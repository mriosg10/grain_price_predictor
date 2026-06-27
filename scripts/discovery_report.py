#!/usr/bin/env python3
"""Phase 0 discovery report — summarises what's been downloaded so far.

Reads every parquet file under data/raw/ and prints a table showing
source, dataset name, row count, date range, and key column stats.

Usage:
    python scripts/discovery_report.py
    python scripts/discovery_report.py --export discovery_report.csv
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pandas as pd
import click
from rich.console import Console
from rich.table import Table

from grain_price_predictor.utils.storage import RAW_DIR

console = Console()


def _date_range(df: pd.DataFrame) -> str:
    date_cols = [c for c in df.columns if "date" in c.lower() or "fecha" in c.lower()]
    if not date_cols:
        return "—"
    col = date_cols[0]
    try:
        s = pd.to_datetime(df[col], errors="coerce").dropna()
        if s.empty:
            return "—"
        return f"{s.min().date()} → {s.max().date()}"
    except Exception:
        return "—"


def _key_cols(df: pd.DataFrame) -> str:
    numeric = df.select_dtypes("number").columns.tolist()
    return ", ".join(numeric[:4]) + ("…" if len(numeric) > 4 else "")


@click.command()
@click.option("--export", default=None, help="Save report as CSV at this path.")
def main(export: str | None) -> None:
    parquets = sorted(RAW_DIR.rglob("*.parquet"))

    if not parquets:
        console.print(
            f"[yellow]No parquet files found under {RAW_DIR}.\n"
            "Run  python scripts/download_all.py  first.[/yellow]"
        )
        return

    rows: list[dict] = []
    table = Table(title="Phase 0 — Data Discovery Report", show_lines=True)
    table.add_column("Source", style="cyan")
    table.add_column("Dataset", style="white")
    table.add_column("Rows", justify="right", style="green")
    table.add_column("Date range")
    table.add_column("Numeric columns")

    for path in parquets:
        source = path.parent.name
        dataset = path.stem
        try:
            df = pd.read_parquet(path)
            row_count = len(df)
            date_range = _date_range(df)
            key_cols = _key_cols(df)
        except Exception as exc:
            row_count = -1
            date_range = f"ERROR: {exc}"
            key_cols = ""

        table.add_row(source, dataset, f"{row_count:,}", date_range, key_cols)
        rows.append(
            {
                "source": source,
                "dataset": dataset,
                "rows": row_count,
                "date_range": date_range,
                "numeric_columns": key_cols,
            }
        )

    console.print(table)
    console.print(f"\n[dim]Total files: {len(parquets)}   Raw data dir: {RAW_DIR}[/dim]")

    if export:
        pd.DataFrame(rows).to_csv(export, index=False)
        console.print(f"[green]Exported → {export}[/green]")


if __name__ == "__main__":
    main()
