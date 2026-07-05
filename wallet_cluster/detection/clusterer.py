"""ウォレットクラスター検出ロジック。

仮説: 「資金力がバラバラなのに、同日内に同じ銘柄を購入しているアドレス群」は
共通の情報源を持つ可能性が高い。

手順:
  1. 同一ウィンドウ(UTC暦日 or 24hローリング)内に同銘柄を買ったウォレットペアを列挙
  2. 共通購入銘柄数 >= min_token_overlap のペアをエッジとしてグラフを構築
  3. 連結成分をクラスター候補とし、資金力の変動係数(CV)等でフィルタ・スコアリング
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import pandas as pd

from ..config import DetectionParams


@dataclass
class CoBuyEvent:
    """あるウォレットペアが同一ウィンドウ内に同銘柄を買った事象。"""

    token_mint: str
    window_key: str
    event_time: pd.Timestamp  # ペア双方の購入が揃った時刻(遅い方)


@dataclass
class DetectionResult:
    clusters: pd.DataFrame        # 1行 = 1クラスター(スコア・根拠付き)
    wallets: pd.DataFrame         # 1行 = 1ウォレット(所属クラスターと資金指標)
    signals: pd.DataFrame         # 1行 = 1シグナル(クラスター×銘柄×検出日時)→ バックテスト入力


class _UnionFind:
    def __init__(self):
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def detect_clusters(
    trades: pd.DataFrame,
    params: DetectionParams = DetectionParams(),
    balances: dict[str, float] | None = None,
) -> DetectionResult:
    """trades(共通スキーマ)からクラスターを検出する。

    balances: {wallet: USD残高}。funding_metric="balance" のとき必須。
    """
    df = _prepare(trades, params)
    funding = _funding_metric(df, params, balances)
    df = _exclude_whales(df, funding, params)

    pair_events = _collect_pair_events(df, params)
    uf = _UnionFind()
    edges: dict[tuple[str, str], list[CoBuyEvent]] = {}
    for pair, events in pair_events.items():
        overlap = len({e.token_mint for e in events})
        if overlap >= params.min_token_overlap:
            edges[pair] = events
            uf.union(*pair)

    members: dict[str, list[str]] = {}
    for pair in edges:
        for w in pair:
            root = uf.find(w)
            members.setdefault(root, [])
            if w not in members[root]:
                members[root].append(w)

    cluster_rows, wallet_rows, signal_rows = [], [], []
    for i, (_, wallets) in enumerate(
        sorted(members.items(), key=lambda kv: -len(kv[1])), start=1
    ):
        if len(wallets) < params.min_cluster_size:
            continue
        cluster_id = f"C{i:03d}"
        cluster_edges = {p: ev for p, ev in edges.items() if p[0] in wallets and p[1] in wallets}
        all_events = [e for ev in cluster_edges.values() for e in ev]
        tokens = sorted({e.token_mint for e in all_events})
        avg_overlap = (
            sum(len({e.token_mint for e in ev}) for ev in cluster_edges.values())
            / len(cluster_edges)
        )
        cv = _cv([funding.get(w, 0.0) for w in wallets])
        qualifies = cv >= params.min_funding_cv
        first_seen = min(e.event_time for e in all_events)
        last_seen = max(e.event_time for e in all_events)
        score = round(avg_overlap * math.log2(1 + len(wallets)) * (1 + min(cv, 2.0)), 3)

        cluster_rows.append(
            {
                "cluster_id": cluster_id,
                "n_wallets": len(wallets),
                "wallets": "|".join(sorted(wallets)),
                "n_shared_tokens": len(tokens),
                "shared_tokens": "|".join(tokens),
                "avg_pair_overlap": round(avg_overlap, 2),
                "funding_cv": round(cv, 3),
                "funding_metric": params.funding_metric,
                "qualifies": qualifies,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "score": score,
            }
        )
        for w in wallets:
            wallet_rows.append(
                {
                    "cluster_id": cluster_id,
                    "wallet": w,
                    "funding_usd": round(funding.get(w, 0.0), 2),
                    "qualifies": qualifies,
                }
            )
        if qualifies:
            # クラスター内で >=2 ウォレットが同銘柄を同ウィンドウ購入した事象をシグナル化
            by_token_window: dict[tuple[str, str], list[CoBuyEvent]] = {}
            for e in all_events:
                by_token_window.setdefault((e.token_mint, e.window_key), []).append(e)
            for (token, window_key), evs in by_token_window.items():
                signal_rows.append(
                    {
                        "cluster_id": cluster_id,
                        "token_mint": token,
                        "window_key": window_key,
                        "detect_time": max(e.event_time for e in evs),
                        "n_cobuy_pairs": len(evs),
                    }
                )

    return DetectionResult(
        clusters=pd.DataFrame(cluster_rows).sort_values("score", ascending=False).reset_index(drop=True)
        if cluster_rows
        else pd.DataFrame(),
        wallets=pd.DataFrame(wallet_rows),
        signals=pd.DataFrame(signal_rows).sort_values("detect_time").reset_index(drop=True)
        if signal_rows
        else pd.DataFrame(),
    )


def _prepare(trades: pd.DataFrame, params: DetectionParams) -> pd.DataFrame:
    df = trades.copy()
    df["block_time"] = pd.to_datetime(df["block_time"], utc=True, format="mixed")
    df = df[df["amount_usd"] >= params.min_trade_usd]
    return df.sort_values("block_time").reset_index(drop=True)


def _funding_metric(
    df: pd.DataFrame, params: DetectionParams, balances: dict[str, float] | None
) -> dict[str, float]:
    if params.funding_metric == "balance":
        if not balances:
            raise ValueError('funding_metric="balance" には balances(残高辞書/CSV)が必要です')
        return dict(balances)
    if params.funding_metric == "median_buy_usd":
        return df.groupby("trader_id")["amount_usd"].median().to_dict()
    raise ValueError(f"unknown funding_metric: {params.funding_metric}")


def _exclude_whales(
    df: pd.DataFrame, funding: dict[str, float], params: DetectionParams
) -> pd.DataFrame:
    if not params.exclude_whales:
        return df
    excluded = set(params.whale_addresses)
    excluded |= {w for w, v in funding.items() if v > params.whale_funding_threshold_usd}
    return df[~df["trader_id"].isin(excluded)]


def _collect_pair_events(
    df: pd.DataFrame, params: DetectionParams
) -> dict[tuple[str, str], list[CoBuyEvent]]:
    if params.window_mode == "utc_day":
        return _pairs_utc_day(df, params)
    if params.window_mode == "rolling_24h":
        return _pairs_rolling_24h(df, params)
    raise ValueError(f"unknown window_mode: {params.window_mode}")


def _pairs_utc_day(
    df: pd.DataFrame, params: DetectionParams
) -> dict[tuple[str, str], list[CoBuyEvent]]:
    pair_events: dict[tuple[str, str], list[CoBuyEvent]] = {}
    day = df["block_time"].dt.strftime("%Y-%m-%d")
    for (token, window_key), g in df.groupby([df["token_mint"], day]):
        first_buy = g.groupby("trader_id")["block_time"].min()
        wallets = sorted(first_buy.index)
        if len(wallets) < 2 or len(wallets) > params.max_buyers_per_window:
            continue
        for a, b in combinations(wallets, 2):
            event_time = max(first_buy[a], first_buy[b])
            pair_events.setdefault((a, b), []).append(CoBuyEvent(token, window_key, event_time))
    return pair_events


def _pairs_rolling_24h(
    df: pd.DataFrame, params: DetectionParams
) -> dict[tuple[str, str], list[CoBuyEvent]]:
    pair_events: dict[tuple[str, str], list[CoBuyEvent]] = {}
    window = pd.Timedelta(hours=24)
    for token, g in df.groupby("token_mint"):
        # ウォレットごとの初回購入時刻のみ使う(同一ウォレットの連打で膨れないように)
        buys = (
            g.groupby("trader_id")["block_time"].min().sort_values().reset_index()
        )
        times = buys["block_time"].tolist()
        wallets = buys["trader_id"].tolist()
        left = 0
        for i in range(len(buys)):
            while times[i] - times[left] > window:
                left += 1
            span_size = i - left + 1
            if span_size < 2 or span_size > params.max_buyers_per_window:
                continue
            window_key = times[i].strftime("%Y-%m-%d")
            for j in range(left, i):
                a, b = sorted((wallets[j], wallets[i]))
                pair_events.setdefault((a, b), []).append(
                    CoBuyEvent(token, window_key, times[i])
                )
    return pair_events


def _cv(values: list[float]) -> float:
    """変動係数(母標準偏差 / 平均)。平均0や要素1個は0を返す。"""
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    if mean == 0:
        return 0.0
    var = sum((v - mean) ** 2 for v in values) / len(values)
    return math.sqrt(var) / mean
