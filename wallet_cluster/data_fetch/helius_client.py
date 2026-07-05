"""Helius API クライアント(無料枠 100万クレジット/月)。

役割:
  - ウォレット残高の取得(資金力 CV の算出・クジラ判定用)
  - ウォレット単位のパース済みスワップ履歴(将来の準リアルタイム検知・深掘り用)

Enhanced Transactions API は deprecated 扱いだが現状動作する。後継の
getTransactionsForAddress は有料プラン限定のため、フェーズ1では使用しない。
"""
from __future__ import annotations

import logging
import time

import requests

from ..config import HELIUS_API_KEY

logger = logging.getLogger(__name__)

RPC_URL = "https://mainnet.helius-rpc.com"
ENHANCED_URL = "https://api-mainnet.helius-rpc.com/v0/addresses/{address}/transactions"
# 無料枠は Enhanced API 2 req/s のため保守的に待つ
REQUEST_INTERVAL_SEC = 0.6
LAMPORTS_PER_SOL = 1_000_000_000


class HeliusError(RuntimeError):
    pass


def _key(api_key: str | None) -> str:
    key = api_key or HELIUS_API_KEY
    if not key:
        raise HeliusError("HELIUS_API_KEY が未設定です(.env を確認してください)")
    return key


def get_sol_balance(address: str, api_key: str | None = None) -> float:
    """SOL 残高(単位: SOL)を返す。"""
    resp = requests.post(
        f"{RPC_URL}/?api-key={_key(api_key)}",
        json={"jsonrpc": "2.0", "id": 1, "method": "getBalance", "params": [address]},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["result"]["value"] / LAMPORTS_PER_SOL


def get_sol_balances(addresses: list[str], api_key: str | None = None) -> dict[str, float]:
    """複数ウォレットの SOL 残高をまとめて取得する(レート制限に配慮して逐次)。"""
    balances: dict[str, float] = {}
    for addr in addresses:
        try:
            balances[addr] = get_sol_balance(addr, api_key)
        except Exception as exc:  # 個別失敗はスキップして続行
            logger.warning("balance fetch failed for %s: %s", addr, exc)
        time.sleep(REQUEST_INTERVAL_SEC)
    return balances


def get_parsed_swaps(
    address: str,
    max_transactions: int = 500,
    api_key: str | None = None,
) -> list[dict]:
    """ウォレットのパース済みスワップ履歴を新しい順に取得する。

    1リクエスト最大100件・100クレジット。before シグネチャでページング。
    """
    swaps: list[dict] = []
    before: str | None = None
    while len(swaps) < max_transactions:
        params = {"api-key": _key(api_key), "type": "SWAP", "limit": 100}
        if before:
            params["before"] = before
        resp = requests.get(ENHANCED_URL.format(address=address), params=params, timeout=60)
        if resp.status_code == 429:
            time.sleep(2)
            continue
        resp.raise_for_status()
        batch = resp.json()
        if not batch:
            break
        swaps.extend(batch)
        before = batch[-1]["signature"]
        time.sleep(REQUEST_INTERVAL_SEC)
    return swaps[:max_transactions]
