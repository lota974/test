"""CLI エントリポイント。

使い方(リポジトリ直下で実行):
  python -m wallet_cluster sample    # 合成サンプルデータ生成(APIキー不要)
  python -m wallet_cluster fetch     # Dune から実データ取得(要 .env)
  python -m wallet_cluster detect    # クラスター検出 → results/ に CSV 出力
  python -m wallet_cluster backtest  # 検出シグナルのバックテスト → CSV + PNG
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from .config import BacktestParams, DetectionParams
from .data_fetch.dune_client import fetch_trades, load_trades_csv
from .data_fetch.prices import CsvPriceProvider, DefiLlamaPriceProvider
from .detection.clusterer import detect_clusters
from .backtest import engine
from . import sample_data

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def _load_balances(path: str | None) -> dict[str, float] | None:
    if not path:
        return None
    df = pd.read_csv(path)
    return dict(zip(df["wallet"], df["balance_usd"]))


def cmd_sample(args: argparse.Namespace) -> None:
    paths = sample_data.generate(args.out_dir, seed=args.seed)
    for name, p in paths.items():
        print(f"  {name}: {p}")


def cmd_fetch(args: argparse.Namespace) -> None:
    df = fetch_trades(
        start_date=args.start,
        end_date=args.end,
        min_trade_usd=args.min_trade_usd,
        min_token_volume_usd=args.min_token_volume_usd,
        max_token_volume_usd=args.max_token_volume_usd,
        cache_dir=args.cache_dir,
        force=args.force,
    )
    print(f"取得: {len(df)} 行 / 銘柄 {df['token_mint'].nunique()} / ウォレット {df['trader_id'].nunique()}")


def _detection_params(args: argparse.Namespace) -> DetectionParams:
    whale_addresses: tuple[str, ...] = ()
    if args.whale_list and Path(args.whale_list).exists():
        whale_addresses = tuple(
            line.strip() for line in Path(args.whale_list).read_text().splitlines() if line.strip()
        )
    return DetectionParams(
        window_mode=args.window,
        min_token_overlap=args.min_overlap,
        min_cluster_size=args.min_cluster_size,
        min_funding_cv=args.min_cv,
        funding_metric=args.funding_metric,
        max_buyers_per_window=args.max_buyers,
        min_trade_usd=args.min_trade_usd,
        exclude_whales=not args.no_whale_exclusion,
        whale_funding_threshold_usd=args.whale_threshold,
        whale_addresses=whale_addresses,
    )


def cmd_detect(args: argparse.Namespace) -> None:
    trades = load_trades_csv(args.trades)
    result = detect_clusters(trades, _detection_params(args), _load_balances(args.balances))

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    result.clusters.to_csv(out_dir / "clusters.csv", index=False)
    result.wallets.to_csv(out_dir / "cluster_wallets.csv", index=False)
    result.signals.to_csv(out_dir / "cluster_signals.csv", index=False)

    print(f"クラスター候補: {len(result.clusters)} 件 (シグナル {len(result.signals)} 件) -> {out_dir}/")
    if not result.clusters.empty:
        cols = ["cluster_id", "n_wallets", "n_shared_tokens", "funding_cv", "qualifies", "score", "last_seen"]
        print(result.clusters[cols].to_string(index=False))


def cmd_backtest(args: argparse.Namespace) -> None:
    trades = load_trades_csv(args.trades)
    signals = pd.read_csv(args.signals) if Path(args.signals).exists() else pd.DataFrame()
    if signals.empty:
        print(f"シグナルがありません: {args.signals}(先に detect を実行してください)")
        return

    params = BacktestParams(
        horizons_days=tuple(args.horizons),
        baseline_top_n=args.top_n,
        price_tolerance_hours=args.price_tolerance_hours,
    )
    provider = CsvPriceProvider(args.prices) if args.prices else DefiLlamaPriceProvider()

    all_signals = pd.concat(
        [
            engine.build_cluster_signals(signals),
            engine.build_baseline_signals(trades, top_n=params.baseline_top_n),
        ],
        ignore_index=True,
    )
    results = engine.evaluate_signals(all_signals, provider, params)
    summary = engine.summarize(results, params)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results.to_csv(out_dir / "backtest_results.csv", index=False)
    summary.to_csv(out_dir / "backtest_summary.csv", index=False)
    chart = engine.plot_summary(summary, out_dir / "backtest_chart.png")

    print(summary.to_string(index=False))
    print(f"出力: {out_dir}/backtest_results.csv, backtest_summary.csv, {chart}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="wallet_cluster")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sample", help="合成サンプルデータ生成(オフライン検証用)")
    p.add_argument("--out-dir", default="data_sample")
    p.add_argument("--seed", type=int, default=42)
    p.set_defaults(func=cmd_sample)

    p = sub.add_parser("fetch", help="Dune から実データ取得(要 DUNE_API_KEY / DUNE_QUERY_ID)")
    p.add_argument("--start", required=True, help="例 2026-06-01")
    p.add_argument("--end", required=True, help="例 2026-07-01(含まない)")
    p.add_argument("--min-trade-usd", type=float, default=10)
    p.add_argument("--min-token-volume-usd", type=float, default=50_000)
    p.add_argument("--max-token-volume-usd", type=float, default=50_000_000)
    p.add_argument("--cache-dir", default="data_cache")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_fetch)

    p = sub.add_parser("detect", help="クラスター検出")
    p.add_argument("--trades", required=True, help="trades CSV(fetch のキャッシュ or sample)")
    p.add_argument("--out-dir", default="results")
    p.add_argument("--window", choices=["utc_day", "rolling_24h"], default="utc_day")
    p.add_argument("--min-overlap", type=int, default=2, help="同日共通購入銘柄数の下限")
    p.add_argument("--min-cluster-size", type=int, default=3)
    p.add_argument("--min-cv", type=float, default=0.5, help="資金力CVの下限")
    p.add_argument("--funding-metric", choices=["median_buy_usd", "balance"], default="median_buy_usd")
    p.add_argument("--balances", help="残高CSV(wallet,balance_usd)。funding-metric=balance 用")
    p.add_argument("--max-buyers", type=int, default=200)
    p.add_argument("--min-trade-usd", type=float, default=10)
    p.add_argument("--no-whale-exclusion", action="store_true")
    p.add_argument("--whale-threshold", type=float, default=250_000)
    p.add_argument("--whale-list", help="除外アドレスのテキストファイル(1行1アドレス)")
    p.set_defaults(func=cmd_detect)

    p = sub.add_parser("backtest", help="検出シグナルの 1/3/7 日リターン検証")
    p.add_argument("--trades", required=True)
    p.add_argument("--signals", default="results/cluster_signals.csv")
    p.add_argument("--prices", help="価格CSV。省略時は DeFiLlama API(実データ用)")
    p.add_argument("--out-dir", default="results")
    p.add_argument("--horizons", type=int, nargs="+", default=[1, 3, 7])
    p.add_argument("--top-n", type=int, default=10, help="ベースラインの上位ウォレット数")
    p.add_argument("--price-tolerance-hours", type=float, default=12)
    p.set_defaults(func=cmd_backtest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
