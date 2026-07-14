"""LightGBM quantile regression model for corn price prediction.

Trains three separate gradient boosting models at P10, P50, P90.
P50 is the point forecast; [P10, P90] is the 80% prediction interval.

The model takes the output of build_corn_features() directly.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd
import lightgbm as lgb
import joblib
from loguru import logger

QUANTILES = (0.10, 0.50, 0.90)

_BASE_PARAMS: dict = {
    "n_estimators":     150,
    "learning_rate":    0.05,
    "num_leaves":       15,
    "min_child_samples": 5,
    "subsample":        0.8,
    "colsample_bytree": 0.8,
    "reg_lambda":       2.0,
    "reg_alpha":        0.5,
    "verbose":         -1,
    "n_jobs":          -1,
}


class CornLGBMModel:
    """Quantile LightGBM — fits three boosters (P10 / P50 / P90)."""

    def __init__(self, params: dict | None = None):
        self._params = {**_BASE_PARAMS, **(params or {})}
        self._models: dict[float, lgb.LGBMRegressor] = {}
        self.feature_names_: list[str] = []

    # ------------------------------------------------------------------
    # Fit / predict
    # ------------------------------------------------------------------

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "CornLGBMModel":
        self.feature_names_ = list(X.columns)
        for q in QUANTILES:
            logger.debug(f"[lgbm] Fitting P{int(q*100):02d}")
            m = lgb.LGBMRegressor(objective="quantile", alpha=q, **self._params)
            m.fit(X, y)
            self._models[q] = m
        return self

    def predict(self, X: pd.DataFrame) -> pd.DataFrame:
        """Return DataFrame with columns p10, p50, p90."""
        cols = {f"p{int(q*100):02d}": m.predict(X)
                for q, m in self._models.items()}
        return pd.DataFrame(cols, index=X.index)

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def feature_importance(self) -> pd.Series:
        m = self._models.get(0.50) or next(iter(self._models.values()))
        return pd.Series(m.feature_importances_, index=self.feature_names_, name="importance").sort_values(
            ascending=False
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        joblib.dump(self, path)
        logger.info(f"[lgbm] Model saved → {path}")

    @classmethod
    def load(cls, path: str | Path) -> "CornLGBMModel":
        return joblib.load(path)
