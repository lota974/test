"""パラメータ定義と .env 読み込み。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

DUNE_API_KEY = os.getenv("DUNE_API_KEY", "")
DUNE_QUERY_ID = os.getenv("DUNE_QUERY_ID", "")
HELIUS_API_KEY = os.getenv("HELIUS_API_KEY", "")


@dataclass(frozen=True)
class DetectionParams:
    """クラスター検出のパラメータ(すべて仮説検証用に外から調整可能)。"""

    # 「同日内」の定義: "utc_day"(UTC暦日) or "rolling_24h"(24時間ローリング)
    window_mode: str = "utc_day"
    # 同一ウォレットペアが同日内に共通購入した銘柄数がこの値以上でエッジ(繋がり)とみなす
    min_token_overlap: int = 2
    # クラスターとして報告する最小ウォレット数
    min_cluster_size: int = 3
    # 「資金力バラバラ」判定: クラスター内の資金指標の変動係数(CV = std/mean)の下限
    min_funding_cv: float = 0.5
    # 資金指標: "median_buy_usd"(買い約定サイズの中央値) or "balance"(残高。balances 指定時)
    funding_metric: str = "median_buy_usd"
    # 1銘柄×1ウィンドウの買い手がこの数を超える場合はシグナル価値なしとしてスキップ(バズ銘柄除外)
    max_buyers_per_window: int = 200
    # ダスト除外: この USD 未満の約定は無視
    min_trade_usd: float = 10.0
    # クジラ除外
    exclude_whales: bool = True
    # 資金指標がこの USD を超えるウォレットをクジラとして除外
    whale_funding_threshold_usd: float = 250_000.0
    # 明示的な除外アドレス(既知のクジラ・CEX・MM 等)
    whale_addresses: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class BacktestParams:
    """バックテストのパラメータ。"""

    horizons_days: tuple[int, ...] = (1, 3, 7)
    # ベースライン「上位アドレスコピー」: 期間内の合計買い金額 上位N ウォレット
    baseline_top_n: int = 10
    # 価格参照時に許容する最大時間ズレ(時間)
    price_tolerance_hours: float = 12.0
