from pathlib import Path
import pandas as pd

# storage.py lives at src/grain_price_predictor/utils/storage.py
# parents[3] = project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"


def save_raw(df: pd.DataFrame, source: str, name: str) -> Path:
    """Save DataFrame as parquet at data/raw/{source}/{name}.parquet."""
    dest = RAW_DIR / source
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / f"{name}.parquet"
    df.to_parquet(path, index=False)
    return path


def load_raw(source: str, name: str) -> pd.DataFrame | None:
    path = RAW_DIR / source / f"{name}.parquet"
    return pd.read_parquet(path) if path.exists() else None
