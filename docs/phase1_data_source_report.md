# フェーズ1 データソース調査レポート

**日付**: 2026-07-05
**目的**: Solana ウォレット単位のスワップ履歴(トークン別・日時別・金額別)を取得できるデータソースの選定

---

## 結論(サマリ)

| # | 結論 |
|---|------|
| 1 | **DeFiLlama API ではウォレット単位のスワップ履歴は取得不可**(プロトコル/チェーン集計データのみ)。ただし価格取得 API(coins.llama.fi)はバックテストの価格系列として無料で有用 |
| 2 | 推奨は **Dune API(無料枠)を主軸**にした構成。キュレート済みテーブル `dex_solana.trades` に「トレーダーのウォレットアドレス・売買トークンの mint・USD 金額・約定時刻・DEX 名」が揃っており、本タスクのクラスタリング+バックテストに必要な粒度と完全一致する |
| 3 | 補助として **Helius(無料枠 100万クレジット/月)** をウォレット個別の深掘り(残高取得・クジラ判定・将来のリアルタイム検知)に使用 |
| 4 | Bitquery / Solscan Pro は無料枠が実用に耐えないため今回は不採用 |

---

## 1. DeFiLlama API の検証結果

- DeFiLlama が提供するのは **TVL・プロトコルランキング・ステーブルコイン供給量・DEX 出来高(プロトコル集計)・トークン価格** であり、単位は「プロトコル/チェーン」。
- **個別ウォレットの取引履歴・スワップ履歴のエンドポイントは存在しない**。公式ドキュメントにも "DeFiLlama tracks protocols and chains; it doesn't label individual wallets" と明記されている。
- → **本タスクの主データソースとしては不可**。
- ただし `coins.llama.fi` の価格 API(現在値 / 任意時点の履歴価格 / チャート)は Solana トークンの mint アドレス指定で無料・キー不要で使えるため、**バックテストの「検出後 1/3/7 日リターン」計算用の価格ソースとして採用候補**。

参考: [DeFiLlama API Docs](https://api-docs.defillama.com/) / [docs.llama.fi](https://docs.llama.fi/)

## 2. 代替データソース比較

### 2.1 Dune Analytics API — ★推奨(主軸)

| 観点 | 内容 |
|------|------|
| データ粒度 | キュレート済みテーブル [`dex_solana.trades`](https://dune.com/data/dex_solana.trades) に **1 スワップ = 1 行** で格納。主なカラム: `trader_id`(ウォレット)、`token_bought_mint_address` / `token_sold_mint_address`、`token_bought_amount` / `token_sold_amount`、`amount_usd`、`block_time`、`project`(Raydium/Orca/Jupiter 等)、`tx_id` |
| クエリ方向 | SQL なので **「トークン→買ったウォレット一覧」という token-first のクエリが可能**。クラスタ検出(同日に同銘柄を買ったアドレス群の抽出)はこの方向が必須であり、他社のウォレット指定型 API より決定的に有利 |
| 無料枠 | 無料プランで **2,500 クレジット/月 + API アクセス込み**。中規模クエリ ≈ 10 クレジットとして月 ~250 回実行可能。「過去 N 日 × 対象銘柄群の全トレード」を 1 クエリでまとめて取得し CSV/ローカルにキャッシュする設計なら十分収まる |
| レート制限 | クレジット制(実行コンピュートに応じて消費)。超過時は $5/100 クレジットの従量。無料枠には結果サイズ等の上限があるため、期間・銘柄でフィルタして分割取得する |
| コスト | $0 で仮説検証可能。本格運用時は Analyst プラン(有料)へ |
| 弱点 | キュレートテーブルの反映に遅延(分〜時間オーダー)があり **リアルタイム検知には不向き**。フェーズ1(過去データでの検証)では問題なし |

参考: [Dune Pricing](https://dune.com/pricing) / [Dune API Billing](https://docs.dune.com/api-reference/overview/billing) / [dex_solana.trades](https://docs.dune.com/data-catalog/curated/trading/solana/solana-dex-trades)

### 2.2 Helius — ★推奨(補助)

| 観点 | 内容 |
|------|------|
| データ粒度 | Enhanced Transactions API `GET /v0/addresses/{address}/transactions?type=SWAP` で **ウォレット指定のパース済みスワップ履歴**(`events.swap`、`tokenTransfers`、日時、金額)を取得可能。1 リクエスト最大 100 件、`before` シグネチャでページング |
| 無料枠 | **100万クレジット/月、クレカ登録不要**。Enhanced API は 2 req/s、100 クレジット/コール → 月 ~10,000 コール |
| 注意点 | ① Enhanced Transactions API は **非推奨(deprecated)扱い**で新機能追加は停止(現状は動作する)。後継の `getTransactionsForAddress` RPC は時刻範囲フィルタ付きだが **有料プラン限定** ② ウォレット指定型のため「どのウォレットを見るか」が先に必要 → **クラスタの初期発見には使えない**(Dune で発見 → Helius で深掘り、の役割分担) |
| 用途 | ウォレット残高・保有状況の取得(資金力 CV の算出、クジラ判定)、検出済みクラスタの追跡、フェーズ2 以降の準リアルタイム検知(Webhooks) |

参考: [Helius Pricing](https://www.helius.dev/pricing) / [Plans](https://www.helius.dev/docs/billing/plans) / [Enhanced Transactions](https://www.helius.dev/docs/enhanced-transactions) / [getTransactionsForAddress](https://www.helius.dev/blog/introducing-gettransactionsforaddress)

### 2.3 Bitquery — 不採用

- GraphQL で Solana DEX トレードを trader 単位・token 単位両方向で引ける点は理想的だが、**無料 Developer プランは「トライアル 1,000 ポイント・10 リクエスト/分・1 リクエスト 10 行まで・個人利用限定」** で、仮説検証にすら足りない。
- 有料は個別見積もり(Commercial)。フェーズ1 の予算感に合わない。

参考: [Bitquery Pricing](https://bitquery.io/pricing) / [Solana DEX Trades API](https://docs.bitquery.io/docs/blockchain/Solana/solana-dextrades/)

### 2.4 Solscan Pro API — 不採用

- `Account DeFi Activities` エンドポイントなど粒度は十分だが、**実質最低 $199/月(Level 2)** で無料 API 枠はテスト程度。
- 参考: [Solscan API Plans](https://solscan.io/apis) / [Pro API Docs](https://docs.solscan.io/api-access/pro-api-endpoints)

## 3. 推奨アーキテクチャ(フェーズ1)

```
[Dune API]  dex_solana.trades を SQL でバルク取得(過去N日 × 対象銘柄)
    │   → ローカルに Parquet/CSV キャッシュ(クレジット節約・再現性)
    ▼
[検出モジュール] 同日(UTC日 or 24hローリング)× トークン重複 × 資金力CV でクラスタ抽出
    │        (残高・クジラ判定が必要な箇所のみ Helius 無料枠で補完)
    ▼
[バックテスト] DeFiLlama 価格API(無料)で検出後 1/3/7 日リターンを計算
             ベースライン(上位アドレスコピー)と比較 → CSV + グラフ出力
```

- **必要な API キー**: Dune(無料登録)、Helius(無料登録)。いずれも `.env` 管理。DeFiLlama 価格 API はキー不要。
- **想定制約**:
  - Dune 無料枠 2,500 クレジット/月 → クエリは「期間×銘柄ユニバース」でまとめ取りし、ローカルキャッシュ必須(実装に組み込む)。
  - 全銘柄・全ウォレットの網羅は無料枠では不可能 → 対象銘柄ユニバース(例: 出来高上位 or 新興トークン群)を絞るパラメータを設ける。
  - Dune の実際の無料枠挙動(結果行数上限等)はアカウント作成後に実測し、乖離があれば報告する。

## 4. 承認いただきたい事項

1. 主データソースを **Dune API(無料枠)**、補助を **Helius(無料枠)**、価格ソースを **DeFiLlama 価格 API** とする構成で実装に着手してよいか。
2. ユーザー側で **Dune / Helius の無料アカウント作成と API キー発行**(いずれも無料・クレカ不要)をお願いしたい。キーは `.env` に配置。
