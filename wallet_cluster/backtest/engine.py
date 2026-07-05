"""バックテスト/検証フレームワーク。

検出クラスターのシグナル(銘柄×検出日時)について、検出後 1/3/7 日の
価格リターンを計測し、ベースライン「上位アドレスコピー」戦略と比較する。
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt

from ..config import BacktestParams
from ..data_fetch.prices import PriceProvider

CLUSTER_STRATEGY = "cluster"
BASELINE_STRATEGY = "baseline_top_traders"

# dataviz 準拠: 系列色は固定順(1=blue, 2=aqua)。テキストはインク色、グリッドは控えめ。
SERIES_COLORS = {CLUSTER_STRATEGY: "#2a78d6", BASELINE_STRATEGY: "#1baf7a"}
INK = "#1a1a19"
MUTED = "#6b6a63"


def build_cluster_signals(signals: pd.DataFrame) -> pd.DataFrame:
    """検出モジュールの signals をバックテスト入力に整形する。"""
    if signals.empty:
        return pd.DataFrame(columns=["strategy", "token_mint", "signal_time"])
    out = signals.rename(columns={"detect_time": "signal_time"})[
        ["token_mint", "signal_time"]
    ].copy()
    out["signal_time"] = pd.to_datetime(out["signal_time"], utc=True, format="mixed")
    out.insert(0, "strategy", CLUSTER_STRATEGY)
    # 同一銘柄×同一UTC日は1シグナルに集約(重み付けの偏りを防ぐ)
    out["_day"] = out["signal_time"].dt.strftime("%Y-%m-%d")
    out = out.groupby(["token_mint", "_day"], as_index=False).agg(
        strategy=("strategy", "first"), signal_time=("signal_time", "max")
    )
    return out[["strategy", "token_mint", "signal_time"]]


def build_baseline_signals(trades: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """ベースライン: 期間内の合計買い金額 上位N ウォレットの買いをシグナル化する。"""
    df = trades.copy()
    df["block_time"] = pd.to_datetime(df["block_time"], utc=True, format="mixed")
    top_wallets = (
        df.groupby("trader_id")["amount_usd"].sum().sort_values(ascending=False).head(top_n).index
    )
    buys = df[df["trader_id"].isin(top_wallets)].copy()
    buys["_day"] = buys["block_time"].dt.strftime("%Y-%m-%d")
    # クラスター側と同じ粒度(銘柄×UTC日)に集約して公平に比較する
    out = buys.groupby(["token_mint", "_day"], as_index=False).agg(
        signal_time=("block_time", "max")
    )
    out.insert(0, "strategy", BASELINE_STRATEGY)
    return out[["strategy", "token_mint", "signal_time"]]


def evaluate_signals(
    signals: pd.DataFrame,
    prices: PriceProvider,
    params: BacktestParams = BacktestParams(),
) -> pd.DataFrame:
    """各シグナルの検出後リターンを計算する。価格欠損のシグナルは NaN のまま残す。"""
    rows = []
    for _, s in signals.iterrows():
        t0 = pd.Timestamp(s["signal_time"])
        p0 = prices.get_price(s["token_mint"], t0, params.price_tolerance_hours)
        row = {
            "strategy": s["strategy"],
            "token_mint": s["token_mint"],
            "signal_time": t0,
            "entry_price": p0,
        }
        for h in params.horizons_days:
            ph = (
                prices.get_price(s["token_mint"], t0 + pd.Timedelta(days=h), params.price_tolerance_hours)
                if p0
                else None
            )
            row[f"ret_{h}d"] = (ph / p0 - 1) if (p0 and ph) else None
        rows.append(row)
    return pd.DataFrame(rows)


def summarize(results: pd.DataFrame, params: BacktestParams = BacktestParams()) -> pd.DataFrame:
    """戦略×ホライズンの集計(件数・平均・中央値・勝率)。"""
    rows = []
    for strategy, g in results.groupby("strategy"):
        for h in params.horizons_days:
            r = pd.to_numeric(g[f"ret_{h}d"], errors="coerce").dropna()
            rows.append(
                {
                    "strategy": strategy,
                    "horizon_days": h,
                    "n_signals": len(r),
                    "mean_return": round(r.mean(), 4) if len(r) else None,
                    "median_return": round(r.median(), 4) if len(r) else None,
                    "win_rate": round((r > 0).mean(), 4) if len(r) else None,
                    "std": round(r.std(ddof=0), 4) if len(r) else None,
                }
            )
    return pd.DataFrame(rows)


def plot_summary(summary: pd.DataFrame, out_path: str | Path) -> Path:
    """平均リターンと勝率の比較チャート(PNG)を保存する。"""
    out_path = Path(out_path)
    strategies = [s for s in (CLUSTER_STRATEGY, BASELINE_STRATEGY) if s in set(summary["strategy"])]
    horizons = sorted(summary["horizon_days"].unique())

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2), facecolor="white")
    # 注: 日本語フォント未導入の環境でも崩れないようチャート内テキストは英語
    metrics = [("mean_return", "Mean return", "{:+.1%}"), ("win_rate", "Win rate", "{:.0%}")]
    bar_w = 0.32
    for ax, (metric, title, fmt) in zip(axes, metrics):
        for si, strategy in enumerate(strategies):
            g = summary[summary["strategy"] == strategy].set_index("horizon_days")
            xs = [hi + (si - (len(strategies) - 1) / 2) * (bar_w + 0.04) for hi in range(len(horizons))]
            ys = [g.loc[h, metric] if h in g.index else 0 for h in horizons]
            ys = [0 if pd.isna(y) else y for y in ys]
            ax.bar(xs, ys, width=bar_w, color=SERIES_COLORS[strategy], label=strategy)
            for x, y in zip(xs, ys):
                ax.annotate(
                    fmt.format(y),
                    (x, y),
                    ha="center",
                    va="bottom" if y >= 0 else "top",
                    fontsize=9,
                    color=INK,
                )
        ax.set_xticks(range(len(horizons)))
        ax.set_xticklabels([f"+{h}d" for h in horizons], color=INK)
        ax.set_title(title, color=INK, fontsize=11)
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.grid(axis="y", color="#e5e4dd", linewidth=0.6)
        ax.set_axisbelow(True)
        for spine in ("top", "right", "left"):
            ax.spines[spine].set_visible(False)
        ax.spines["bottom"].set_color(MUTED)
        ax.tick_params(colors=MUTED)
    axes[0].legend(frameon=False, fontsize=9)
    fig.suptitle("Cluster detection vs top-trader copy (post-signal returns)", color=INK, fontsize=12)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)
    return out_path
