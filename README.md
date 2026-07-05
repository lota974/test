# Solana ウォレットクラスター検出システム(コピトレbot フェーズ1)

「資金力がバラバラなのに、同日内に同じ銘柄を購入しているアドレス群」を検出し、
そのシグナルに検出後 1/3/7 日の価格エッジがあるかを検証するツール群です。
**このフェーズでは検出とバックテストのみ**(実売買・ウォレット接続なし)。

データソースの選定理由は [docs/phase1_data_source_report.md](docs/phase1_data_source_report.md) を参照
(結論: Dune API 主軸 + Helius 補助 + DeFiLlama 価格 API)。

## セットアップ(Windows / VSCode)

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # DUNE_API_KEY / DUNE_QUERY_ID / HELIUS_API_KEY を記入
```

実データを使う場合の事前準備:

1. [Dune](https://dune.com) で無料アカウントを作成し、[設定画面](https://dune.com/settings/api) で API キーを発行
2. Dune 上で新規クエリを作成し、`sql/dune_solana_dex_trades.sql` の内容を貼り付けて保存。
   URL に含まれるクエリ ID(数字)を `.env` の `DUNE_QUERY_ID` に設定
3. (任意)[Helius](https://dashboard.helius.dev) で無料 API キーを発行(残高ベースの資金力CV・クジラ判定に使用)

## クイックスタート(APIキー不要のオフライン検証)

```powershell
python -m wallet_cluster sample                                            # 合成データ生成
python -m wallet_cluster detect --trades data_sample/trades.csv `
    --funding-metric balance --balances data_sample/balances.csv           # クラスター検出
python -m wallet_cluster backtest --trades data_sample/trades.csv `
    --prices data_sample/prices.csv                                        # バックテスト
python -m pytest tests/                                                    # 単体テスト
```

合成データには「資金力 $500〜$200,000 とバラバラな6ウォレットが ALPHA 銘柄3つを
同日に共同購入し、その後価格が上昇する」パターンが植え込まれており、
検出→バックテストの全経路がオフラインで確認できます。

## 実データでの実行

```powershell
# 1. データ取得(Dune 無料枠節約のため期間・銘柄ユニバースは必ず絞る)
python -m wallet_cluster fetch --start 2026-06-01 --end 2026-06-15
# → data_cache/ に CSV キャッシュ(同一パラメータの再実行は API を呼ばない)

# 2. クラスター検出
python -m wallet_cluster detect --trades data_cache/trades_2026-06-01_2026-06-15_xxxx.csv

# 3. バックテスト(--prices 省略時は DeFiLlama 価格 API を使用)
python -m wallet_cluster backtest --trades data_cache/trades_....csv
```

## モジュール構成

| モジュール | 役割 | 単体確認 |
|---|---|---|
| `wallet_cluster/data_fetch/` | Dune(スワップ履歴)/ Helius(残高・ウォレット深掘り)/ DeFiLlama(価格)+ ローカルキャッシュ | `fetch` コマンド / `sample` の生成データ |
| `wallet_cluster/detection/` | 同日共同購入グラフ → 連結成分 → 資金力CVフィルタ → スコアリング | `detect` コマンド / `tests/test_clusterer.py` |
| `wallet_cluster/backtest/` | 検出後 1/3/7 日リターン計測、ベースライン(上位アドレスコピー)比較、CSV+グラフ出力 | `backtest` コマンド / `tests/test_backtest.py` |

## 検出ロジックの主要パラメータ(`detect` のオプション)

| パラメータ | 既定値 | 意味 |
|---|---|---|
| `--window` | `utc_day` | 「同日内」の定義。`utc_day`(UTC暦日)/ `rolling_24h`(24時間ローリング) |
| `--min-overlap` | `2` | ウォレットペアを繋ぐのに必要な「同日共通購入銘柄数」 |
| `--min-cluster-size` | `3` | 報告する最小クラスター人数 |
| `--min-cv` | `0.5` | 「資金力バラバラ」の下限(変動係数 CV = std/mean) |
| `--funding-metric` | `median_buy_usd` | 資金力の定義。買いサイズ中央値 or `balance`(残高CSV/Helius) |
| `--max-buyers` | `200` | 1銘柄×1ウィンドウの買い手上限(バズ銘柄はシグナル価値なしとして除外) |
| `--whale-threshold` | `250000` | 資金力がこの USD 超のウォレットをクジラとして除外 |
| `--whale-list` | ― | 既知クジラ/CEX/MM の明示的除外リスト(1行1アドレス) |
| `--no-whale-exclusion` | ― | クジラ除外を無効化 |

## 出力

- `results/clusters.csv` — クラスター候補一覧(ウォレット、重複銘柄数、資金CV、検出日時、スコア)
- `results/cluster_wallets.csv` — ウォレット単位の内訳(所属クラスター・資金指標)
- `results/cluster_signals.csv` — バックテスト入力となるシグナル(クラスター×銘柄×検出日時)
- `results/backtest_results.csv` — シグナル単位のリターン
- `results/backtest_summary.csv` — 戦略×ホライズンの集計(平均・中央値・勝率)
- `results/backtest_chart.png` — 提案手法 vs ベースラインの比較グラフ

スコアは `平均ペア重複銘柄数 × log2(1+人数) × (1+min(CV,2))` で、
「重複が濃く」「人数が多く」「資金力が散っている」クラスターほど高くなります。

## 既知の制約・注意

- Dune 無料枠は 2,500 クレジット/月。`fetch` は必ずキャッシュを使い、期間 (`--start/--end`) と
  銘柄ユニバース(出来高 `--min/--max-token-volume-usd`)を絞って実行してください。
- Dune のキュレートテーブルは反映に遅延があるため、リアルタイム検知はフェーズ2(Helius Webhooks)で扱います。
- `--funding-metric balance` で残高が不明なウォレットは 0 として扱われ CV が過大になることがあります。
  実運用では検出候補に対して Helius(`data_fetch/helius_client.py`)で残高を補完してください。
- 合成データのバックテスト結果はパイプライン検証用であり、エッジの実証は実データで行う必要があります。
