"""検出ロジックの単体テスト(APIキー不要・オフライン)。"""
import pandas as pd
import pytest

from wallet_cluster.config import DetectionParams
from wallet_cluster.detection.clusterer import detect_clusters, _cv


def _trade(t, wallet, token, usd=100.0):
    return {
        "block_time": t,
        "trader_id": wallet,
        "token_mint": token,
        "token_symbol": token,
        "amount_usd": usd,
        "tx_id": f"tx-{wallet}-{token}-{t}",
    }


def _planted_trades():
    """3ウォレットが2銘柄を同日共同購入(サイズはバラバラ)+ ノイズ。"""
    rows = [
        # 同日(6/10)に TOKA を共同購入
        _trade("2026-06-10T01:00:00Z", "A", "TOKA", 50),
        _trade("2026-06-10T05:00:00Z", "B", "TOKA", 2_000),
        _trade("2026-06-10T09:00:00Z", "C", "TOKA", 30_000),
        # 同日(6/15)に TOKB を共同購入
        _trade("2026-06-15T02:00:00Z", "A", "TOKB", 60),
        _trade("2026-06-15T06:00:00Z", "B", "TOKB", 1_800),
        _trade("2026-06-15T20:00:00Z", "C", "TOKB", 28_000),
        # ノイズ: 単独購入
        _trade("2026-06-11T00:00:00Z", "X", "TOKC", 500),
        _trade("2026-06-12T00:00:00Z", "Y", "TOKD", 700),
    ]
    return pd.DataFrame(rows)


def test_detects_planted_cluster_utc_day():
    result = detect_clusters(_planted_trades(), DetectionParams(window_mode="utc_day"))
    assert len(result.clusters) == 1
    c = result.clusters.iloc[0]
    assert set(c["wallets"].split("|")) == {"A", "B", "C"}
    assert c["n_shared_tokens"] == 2
    assert bool(c["qualifies"])  # サイズがバラバラなので CV は高い
    assert not result.signals.empty
    assert set(result.signals["token_mint"]) == {"TOKA", "TOKB"}


def test_min_overlap_threshold():
    # 共通購入が1銘柄しかないので min_token_overlap=2 では検出されない
    df = _planted_trades()
    df = df[df["token_mint"] != "TOKB"]
    result = detect_clusters(df, DetectionParams(min_token_overlap=2))
    assert result.clusters.empty
    result1 = detect_clusters(df, DetectionParams(min_token_overlap=1))
    assert len(result1.clusters) == 1


def test_utc_day_boundary_vs_rolling_24h():
    # 23:00 と 翌日01:00 の購入: UTC日では別ウィンドウ、24hローリングでは同一
    rows = []
    for token in ("TOKA", "TOKB"):
        day = "2026-06-10" if token == "TOKA" else "2026-06-15"
        next_day = "2026-06-11" if token == "TOKA" else "2026-06-16"
        rows += [
            _trade(f"{day}T23:00:00Z", "A", token, 100),
            _trade(f"{next_day}T01:00:00Z", "B", token, 5_000),
            _trade(f"{next_day}T01:30:00Z", "C", token, 90_000),
        ]
    df = pd.DataFrame(rows)
    utc = detect_clusters(df, DetectionParams(window_mode="utc_day", min_cluster_size=3))
    rolling = detect_clusters(df, DetectionParams(window_mode="rolling_24h", min_cluster_size=3))
    # UTC日モード: A は B,C と日付が割れて 3人クラスターにならない
    assert utc.clusters.empty
    assert len(rolling.clusters) == 1
    assert set(rolling.clusters.iloc[0]["wallets"].split("|")) == {"A", "B", "C"}


def test_whale_exclusion():
    df = _planted_trades()
    # C の買いサイズ(中央値 29,000)を閾値超えのクジラとして除外
    params = DetectionParams(
        exclude_whales=True, whale_funding_threshold_usd=10_000, min_cluster_size=2
    )
    result = detect_clusters(df, params)
    assert len(result.clusters) == 1
    assert set(result.clusters.iloc[0]["wallets"].split("|")) == {"A", "B"}


def test_explicit_whale_list():
    df = _planted_trades()
    params = DetectionParams(whale_addresses=("B",), min_cluster_size=2)
    result = detect_clusters(df, params)
    assert "B" not in result.clusters.iloc[0]["wallets"]


def test_funding_cv_gate():
    # 全員ほぼ同サイズ → CV が低く qualifies=False、シグナルなし
    rows = [
        _trade("2026-06-10T01:00:00Z", "A", "TOKA", 1000),
        _trade("2026-06-10T05:00:00Z", "B", "TOKA", 1050),
        _trade("2026-06-10T09:00:00Z", "C", "TOKA", 980),
        _trade("2026-06-15T02:00:00Z", "A", "TOKB", 1010),
        _trade("2026-06-15T06:00:00Z", "B", "TOKB", 990),
        _trade("2026-06-15T20:00:00Z", "C", "TOKB", 1020),
    ]
    result = detect_clusters(pd.DataFrame(rows), DetectionParams(min_funding_cv=0.5))
    assert len(result.clusters) == 1
    assert not bool(result.clusters.iloc[0]["qualifies"])
    assert result.signals.empty


def test_balance_metric_requires_balances():
    with pytest.raises(ValueError):
        detect_clusters(_planted_trades(), DetectionParams(funding_metric="balance"))


def test_cv():
    assert _cv([100, 100, 100]) == 0
    assert _cv([]) == 0
    assert _cv([50, 30_000]) > 0.9
