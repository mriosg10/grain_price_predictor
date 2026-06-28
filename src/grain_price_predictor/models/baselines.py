"""Baseline models for corn price prediction.

These set the performance floor that any ML model must beat.
All models follow a fit(X, y) / predict(X) interface compatible
with the backtest runner.
"""
from __future__ import annotations
import numpy as np
import pandas as pd


class NaiveSeasonalModel:
    """Predict same calendar month from one year ago (naïve seasonal)."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "NaiveSeasonalModel":
        self._y = y.copy()
        return self

    def predict(self, X: pd.DataFrame) -> pd.Series:
        preds = []
        for d in X.index:
            prev = d - pd.DateOffset(years=1)
            candidates = self._y.index[self._y.index <= prev]
            preds.append(float(self._y.iloc[self._y.index.get_loc(candidates[-1])])
                         if len(candidates) else np.nan)
        return pd.Series(preds, index=X.index, name="naive_seasonal")


class SeasonalMeanModel:
    """Predict the in-sample mean for each calendar month."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "SeasonalMeanModel":
        self._means = y.groupby(y.index.month).mean()
        return self

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(
            [self._means.get(d.month, np.nan) for d in X.index],
            index=X.index,
            name="seasonal_mean",
        )


class RandomWalkModel:
    """Predict last known value (persistence / random-walk baseline)."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "RandomWalkModel":
        self._last = float(y.iloc[-1])
        return self

    def predict(self, X: pd.DataFrame) -> pd.Series:
        return pd.Series(self._last, index=X.index, name="random_walk")


class TrendSeasonalModel:
    """Linear trend + monthly seasonality (OLS, no external features)."""

    def fit(self, X: pd.DataFrame, y: pd.Series) -> "TrendSeasonalModel":
        from sklearn.linear_model import Ridge
        from sklearn.preprocessing import OneHotEncoder

        self._enc = OneHotEncoder(sparse_output=False, drop="first")
        months = y.index.month.values.reshape(-1, 1)
        M = self._enc.fit_transform(months)
        t = np.arange(len(y)).reshape(-1, 1)
        Xmat = np.hstack([t, M])
        self._t0 = len(y)
        self._model = Ridge().fit(Xmat, y.values)
        return self

    def _make_X(self, index: pd.DatetimeIndex) -> np.ndarray:
        months = index.month.values.reshape(-1, 1)
        M = self._enc.transform(months)
        n = len(index)
        t = np.arange(self._t0, self._t0 + n).reshape(-1, 1)
        return np.hstack([t, M])

    def predict(self, X: pd.DataFrame) -> pd.Series:
        Xmat = self._make_X(X.index)
        return pd.Series(self._model.predict(Xmat), index=X.index, name="trend_seasonal")
