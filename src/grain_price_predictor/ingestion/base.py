from datetime import date
import pandas as pd
from loguru import logger
from grain_price_predictor.utils.storage import save_raw, load_raw


class BaseIngester:
    """Shared interface for all data ingesters."""

    source: str = ""

    def save(self, df: pd.DataFrame, name: str) -> None:
        path = save_raw(df, self.source, name)
        logger.debug(f"[{self.source}] saved {len(df):,} rows → {path.name}")

    def load(self, name: str) -> pd.DataFrame | None:
        return load_raw(self.source, name)

    def download(
        self,
        start: date = date(2010, 1, 1),
        end: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        raise NotImplementedError
