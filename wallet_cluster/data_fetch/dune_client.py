"""Dune API クライアント。

dex_solana.trades を対象にした保存済みクエリ(sql/dune_solana_dex_trades.sql)を
パラメータ付きで実行し、正規化した trades DataFrame を返す。

無料枠(2,500 クレジット/月)節約のため、結果は data_cache/ に CSV キャッシュされ、
同一パラメータでの再実行は API を呼ばない。
"""
from __future__ import annotations

import hashlib
import logging
import time
from pathlib import Path

import pandas as pd
import requests

from ..config import DUNE_API_KEY, DUNE_QUERY_ID

logger = logging.getLogger(__name__)

API_BASE = "https://api.dune.com/api/v1"
POLL_INTERVAL_SEC = 5
POLL_TIMEOUT_SEC = 600
RESULT_PAGE_LIMIT = 30_000

# 正規化後の trades スキーマ(全モジュール共通)
TRADE_COLUMNS = ["block_time", "trader_id", "token_mint", "token_symbol", "amount_usd", "tx_id"]


class DuneError(RuntimeError):
    pass


def fetch_trades(
    start_date: str,
    end_date: str,
    min_trade_usd: float = 10,
    min_token_volume_usd: float = 50_000,
    max_token_volume_usd: float = 50_000_000,
    cache_dir: str | Path = "data_cache",
    force: bool = False,
    api_key: str | None = None,
    query_id: str | None = None,
) -> pd.DataFrame:
    """期間 [start_date, end_date) の Solana DEX 買い約定を取得する。

    キャッシュがあればそれを返す。force=True で強制再取得。
    """
    api_key = api_key or DUNE_API_KEY
    query_id = query_id or DUNE_QUERY_ID

    params = {
        "start_date": start_date,
        "end_date": end_date,
        "min_trade_usd": min_trade_usd,
        "min_token_volume_usd": min_token_volume_usd,
        "max_token_volume_usd": max_token_volume_usd,
    }
    cache_path = _cache_path(cache_dir, params)
    if cache_path.exists() and not force:
        logger.info("cache hit: %s", cache_path)
        return load_trades_csv(cache_path)

    if not api_key or not query_id:
        raise DuneError(
            "DUNE_API_KEY / DUNE_QUERY_ID が未設定です。.env を設定するか、"
            "オフライン検証には `python -m wallet_cluster sample` の生成データを使ってください。"
        )

    execution_id = _execute(query_id, params, api_key)
    _wait_for_completion(execution_id, api_key)
    df = _download_results(execution_id, api_key)
    df = normalize_trades(df)

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(cache_path, index=False)
    logger.info("fetched %d rows -> %s", len(df), cache_path)
    return df


def normalize_trades(df: pd.DataFrame) -> pd.DataFrame:
    """Dune の生結果を共通スキーマに正規化する。"""
    missing = [c for c in TRADE_COLUMNS if c not in df.columns]
    if missing:
        raise DuneError(f"Dune 結果に想定カラムがありません: {missing}")
    out = df[TRADE_COLUMNS].copy()
    out["block_time"] = pd.to_datetime(out["block_time"], utc=True, format="mixed")
    out["amount_usd"] = pd.to_numeric(out["amount_usd"], errors="coerce")
    out = out.dropna(subset=["block_time", "trader_id", "token_mint", "amount_usd"])
    return out.sort_values("block_time").reset_index(drop=True)


def load_trades_csv(path: str | Path) -> pd.DataFrame:
    """キャッシュ/サンプルの trades CSV を読み込む。"""
    return normalize_trades(pd.read_csv(path))


def _cache_path(cache_dir: str | Path, params: dict) -> Path:
    key = hashlib.md5(repr(sorted(params.items())).encode()).hexdigest()[:10]
    return Path(cache_dir) / f"trades_{params['start_date']}_{params['end_date']}_{key}.csv"


def _headers(api_key: str) -> dict:
    return {"X-Dune-API-Key": api_key}


def _execute(query_id: str, params: dict, api_key: str) -> str:
    resp = requests.post(
        f"{API_BASE}/query/{query_id}/execute",
        headers=_headers(api_key),
        json={"query_parameters": {k: str(v) for k, v in params.items()}, "performance": "medium"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise DuneError(f"execute failed: HTTP {resp.status_code} {resp.text[:300]}")
    return resp.json()["execution_id"]


def _wait_for_completion(execution_id: str, api_key: str) -> None:
    deadline = time.time() + POLL_TIMEOUT_SEC
    while time.time() < deadline:
        resp = requests.get(
            f"{API_BASE}/execution/{execution_id}/status", headers=_headers(api_key), timeout=30
        )
        resp.raise_for_status()
        state = resp.json().get("state", "")
        if state == "QUERY_STATE_COMPLETED":
            return
        if state in ("QUERY_STATE_FAILED", "QUERY_STATE_CANCELLED"):
            raise DuneError(f"query {state}: {resp.json()}")
        time.sleep(POLL_INTERVAL_SEC)
    raise DuneError("Dune クエリがタイムアウトしました(期間・ユニバースを絞ってください)")


def _download_results(execution_id: str, api_key: str) -> pd.DataFrame:
    rows: list[dict] = []
    offset = 0
    while True:
        resp = requests.get(
            f"{API_BASE}/execution/{execution_id}/results",
            headers=_headers(api_key),
            params={"limit": RESULT_PAGE_LIMIT, "offset": offset},
            timeout=120,
        )
        resp.raise_for_status()
        page = resp.json()["result"]["rows"]
        rows.extend(page)
        if len(page) < RESULT_PAGE_LIMIT:
            break
        offset += RESULT_PAGE_LIMIT
    return pd.DataFrame(rows)
