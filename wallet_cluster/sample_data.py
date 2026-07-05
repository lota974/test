"""オフライン動作確認用の合成サンプルデータ生成。

APIキーなしで 検出→バックテスト をエンドツーエンドで検証するためのデータ:
  - ノイズウォレット150個がランダム銘柄をランダムに購入
  - 「植え込みクラスター」6ウォレット(資金力 $50〜$60,000 とバラバラ)が
    ALPHA 銘柄3つを同じUTC日に購入
  - クジラ3ウォレットが巨額でランダム購入(ベースライン戦略が拾う想定)
  - ALPHA 銘柄はクラスター購入日の後に上昇する価格系列を持つ
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

N_NOISE_WALLETS = 150
N_NOISE_TOKENS = 12
N_DAYS = 30
PRICE_BUFFER_DAYS = 8  # 7日後リターン計算用の余白

CLUSTER_WALLETS = [f"CLUSTERW{i}" for i in range(1, 7)]
# 資金力バラバラ(中央値買いサイズの狙い値, USD)
CLUSTER_SIZES = [50, 300, 1_200, 8_000, 25_000, 60_000]
CLUSTER_BALANCES = [500, 3_000, 20_000, 80_000, 200_000, 45_000]
ALPHA_TOKENS = ["ALPHA1", "ALPHA2", "ALPHA3"]
WHALE_WALLETS = ["WHALE1", "WHALE2", "WHALE3"]


def generate(out_dir: str | Path = "data_sample", seed: int = 42) -> dict[str, Path]:
    rng = random.Random(seed)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    noise_tokens = [f"TOK{i:02d}" for i in range(1, N_NOISE_TOKENS + 1)]
    all_tokens = noise_tokens + ALPHA_TOKENS
    trades: list[dict] = []

    def add_trade(wallet: str, token: str, t: datetime, usd: float):
        trades.append(
            {
                "block_time": t.isoformat(),
                "trader_id": wallet,
                "token_mint": token,
                "token_symbol": token,
                "amount_usd": round(usd, 2),
                "tx_id": f"tx{len(trades):06d}",
            }
        )

    # ノイズ: 各ウォレットが期間中に 3〜10 回ランダム購入
    for w in range(N_NOISE_WALLETS):
        wallet = f"NOISEW{w:03d}"
        for _ in range(rng.randint(3, 10)):
            t = start + timedelta(days=rng.uniform(0, N_DAYS), hours=rng.uniform(0, 24))
            add_trade(wallet, rng.choice(all_tokens), t, rng.lognormvariate(5.5, 1.2))

    # クジラ: 巨額購入(合計金額でベースライン上位に入る)
    for wallet in WHALE_WALLETS:
        for _ in range(15):
            t = start + timedelta(days=rng.uniform(0, N_DAYS), hours=rng.uniform(0, 24))
            add_trade(wallet, rng.choice(all_tokens), t, rng.uniform(150_000, 500_000))

    # 植え込みクラスター: ALPHA 銘柄を同じUTC日に共同購入(+ノイズ購入少々)
    alpha_event_days = [5, 12, 20]  # ALPHA1/2/3 の購入日(start からの日数)
    for token, day in zip(ALPHA_TOKENS, alpha_event_days):
        buyers = rng.sample(CLUSTER_WALLETS, 5)  # 毎回6人中5人が参加
        for wallet in buyers:
            size = CLUSTER_SIZES[CLUSTER_WALLETS.index(wallet)]
            t = start + timedelta(days=day, hours=rng.uniform(1, 22))
            add_trade(wallet, token, t, size * rng.uniform(0.8, 1.2))
    for wallet in CLUSTER_WALLETS:  # クラスターにも通常のノイズ購入を混ぜる
        size = CLUSTER_SIZES[CLUSTER_WALLETS.index(wallet)]
        for _ in range(rng.randint(2, 4)):
            t = start + timedelta(days=rng.uniform(0, N_DAYS), hours=rng.uniform(0, 24))
            add_trade(wallet, rng.choice(noise_tokens), t, size * rng.uniform(0.5, 1.5))

    trades_df = pd.DataFrame(trades).sort_values("block_time")

    # 価格系列(日次): ノイズ銘柄はランダムウォーク、ALPHA はイベント日後7日で上昇
    price_rows = []
    for token in all_tokens:
        price = rng.uniform(0.1, 5.0)
        pump_start = (
            alpha_event_days[ALPHA_TOKENS.index(token)] if token in ALPHA_TOKENS else None
        )
        for d in range(N_DAYS + PRICE_BUFFER_DAYS):
            drift = 0.0
            if pump_start is not None and pump_start < d <= pump_start + 7:
                drift = rng.uniform(0.03, 0.06)  # イベント翌日から7日間 +3〜6%/日
            price *= 1 + drift + rng.gauss(0, 0.02)
            price_rows.append(
                {
                    "token_mint": token,
                    "timestamp": (start + timedelta(days=d, hours=12)).isoformat(),
                    "price": round(price, 6),
                }
            )
    prices_df = pd.DataFrame(price_rows)

    balances_df = pd.DataFrame(
        [{"wallet": w, "balance_usd": b} for w, b in zip(CLUSTER_WALLETS, CLUSTER_BALANCES)]
        + [{"wallet": w, "balance_usd": 5_000_000} for w in WHALE_WALLETS]
    )

    paths = {
        "trades": out_dir / "trades.csv",
        "prices": out_dir / "prices.csv",
        "balances": out_dir / "balances.csv",
    }
    trades_df.to_csv(paths["trades"], index=False)
    prices_df.to_csv(paths["prices"], index=False)
    balances_df.to_csv(paths["balances"], index=False)
    return paths
