"""バックテストモジュールの単体テスト(オフライン)。"""
import pandas as pd

from wallet_cluster.config import BacktestParams
from wallet_cluster.backtest import engine
from wallet_cluster.data_fetch.prices import CsvPriceProvider


def _price_csv(tmp_path):
    # TOKA: 100 -> 110 (1d) -> 130 (3d) -> 170 (7d)
    rows = []
    prices = {0: 100, 1: 110, 2: 120, 3: 130, 4: 140, 5: 150, 6: 160, 7: 170}
    for d, p in prices.items():
        rows.append(
            {
                "token_mint": "TOKA",
                "timestamp": f"2026-06-{10 + d:02d}T12:00:00Z",
                "price": p,
            }
        )
    path = tmp_path / "prices.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_evaluate_signals_returns(tmp_path):
    provider = CsvPriceProvider(_price_csv(tmp_path))
    signals = pd.DataFrame(
        [{"strategy": "cluster", "token_mint": "TOKA", "signal_time": "2026-06-10T12:00:00Z"}]
    )
    signals["signal_time"] = pd.to_datetime(signals["signal_time"], utc=True)
    results = engine.evaluate_signals(signals, provider, BacktestParams())
    r = results.iloc[0]
    assert abs(r["ret_1d"] - 0.10) < 1e-9
    assert abs(r["ret_3d"] - 0.30) < 1e-9
    assert abs(r["ret_7d"] - 0.70) < 1e-9


def test_missing_price_gives_nan(tmp_path):
    provider = CsvPriceProvider(_price_csv(tmp_path))
    signals = pd.DataFrame(
        [{"strategy": "cluster", "token_mint": "UNKNOWN", "signal_time": "2026-06-10T12:00:00Z"}]
    )
    signals["signal_time"] = pd.to_datetime(signals["signal_time"], utc=True)
    results = engine.evaluate_signals(signals, provider, BacktestParams())
    assert results.iloc[0]["entry_price"] is None
    summary = engine.summarize(results)
    assert (summary["n_signals"] == 0).all()


def test_baseline_top_n_picks_biggest_wallets():
    rows = []
    for w, usd in [("BIG1", 100_000), ("BIG2", 90_000), ("SMALL", 100)]:
        rows.append(
            {
                "block_time": "2026-06-10T01:00:00Z",
                "trader_id": w,
                "token_mint": f"TOK-{w}",
                "token_symbol": f"TOK-{w}",
                "amount_usd": usd,
                "tx_id": f"tx-{w}",
            }
        )
    signals = engine.build_baseline_signals(pd.DataFrame(rows), top_n=2)
    assert set(signals["token_mint"]) == {"TOK-BIG1", "TOK-BIG2"}
    assert (signals["strategy"] == engine.BASELINE_STRATEGY).all()


def test_cluster_signals_dedupe_same_day():
    signals = pd.DataFrame(
        [
            {"cluster_id": "C001", "token_mint": "TOKA", "window_key": "2026-06-10",
             "detect_time": "2026-06-10T05:00:00Z", "n_cobuy_pairs": 3},
            {"cluster_id": "C002", "token_mint": "TOKA", "window_key": "2026-06-10",
             "detect_time": "2026-06-10T09:00:00Z", "n_cobuy_pairs": 1},
        ]
    )
    out = engine.build_cluster_signals(signals)
    assert len(out) == 1  # 同銘柄×同日は1シグナルに集約
    assert out.iloc[0]["signal_time"] == pd.Timestamp("2026-06-10T09:00:00Z")
