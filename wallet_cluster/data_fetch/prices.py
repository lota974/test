"""価格プロバイダ。

バックテストの「検出後 1/3/7 日リターン」計算に使う。
  - CsvPriceProvider     : ローカル CSV(token_mint, timestamp, price)。オフライン検証・サンプル用
  - DefiLlamaPriceProvider: coins.llama.fi(無料・キー不要)。実データ用
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

logger = logging.getLogger(__name__)

LLAMA_BASE = "https://coins.llama.fi"
REQUEST_INTERVAL_SEC = 0.25


class PriceProvider:
    """timestamp(UTC)時点のトークン価格を返すインターフェース。"""

    def get_price(self, token_mint: str, ts: datetime, tolerance_hours: float = 12.0) -> float | None:
        raise NotImplementedError


class CsvPriceProvider(PriceProvider):
    """CSV(token_mint, timestamp, price)から最近傍価格を返す。"""

    def __init__(self, path: str | Path):
        df = pd.read_csv(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, format="mixed")
        self._series: dict[str, pd.DataFrame] = {
            mint: g.sort_values("timestamp").reset_index(drop=True)
            for mint, g in df.groupby("token_mint")
        }

    def get_price(self, token_mint: str, ts: datetime, tolerance_hours: float = 12.0) -> float | None:
        g = self._series.get(token_mint)
        if g is None or g.empty:
            return None
        ts = pd.Timestamp(ts)
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        idx = (g["timestamp"] - ts).abs().idxmin()
        if abs((g.loc[idx, "timestamp"] - ts).total_seconds()) > tolerance_hours * 3600:
            return None
        return float(g.loc[idx, "price"])


class DefiLlamaPriceProvider(PriceProvider):
    """DeFiLlama 価格 API。呼び出し結果をメモリキャッシュする。"""

    def __init__(self):
        self._cache: dict[tuple[str, int], float | None] = {}

    def get_price(self, token_mint: str, ts: datetime, tolerance_hours: float = 12.0) -> float | None:
        unix_ts = int(ts.replace(tzinfo=ts.tzinfo or timezone.utc).timestamp())
        # 同一トークン×同一時間帯(1h単位)はキャッシュを返す
        cache_key = (token_mint, unix_ts // 3600)
        if cache_key in self._cache:
            return self._cache[cache_key]

        coin = f"solana:{token_mint}"
        try:
            resp = requests.get(
                f"{LLAMA_BASE}/prices/historical/{unix_ts}/{coin}",
                params={"searchWidth": f"{int(tolerance_hours)}h"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json().get("coins", {}).get(coin)
            price = float(data["price"]) if data else None
        except Exception as exc:
            logger.warning("price fetch failed %s @%s: %s", token_mint, ts, exc)
            price = None
        self._cache[cache_key] = price
        time.sleep(REQUEST_INTERVAL_SEC)
        return price
