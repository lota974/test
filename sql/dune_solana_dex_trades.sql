-- Solana DEX スワップ(買い側)取得クエリ
-- Dune (https://dune.com) で新規クエリとして保存し、そのクエリIDを .env の DUNE_QUERY_ID に設定してください。
-- パラメータ(Dune クエリパラメータとして作成):
--   start_date      (text, 例 2026-06-01)
--   end_date        (text, 例 2026-07-01)  ※ end_date は含まない
--   min_trade_usd   (number, 例 10)        1約定あたりの最小USD
--   min_token_volume_usd (number, 例 50000)   期間内の銘柄出来高 下限(閑散銘柄を除外)
--   max_token_volume_usd (number, 例 50000000) 期間内の銘柄出来高 上限(メジャー銘柄を除外)
--
-- 無料枠(2,500クレジット/月)節約のため、期間と銘柄ユニバースを必ず絞ってから実行すること。

WITH token_universe AS (
    SELECT token_bought_mint_address AS token_mint
    FROM dex_solana.trades
    WHERE block_time >= from_iso8601_timestamp('{{start_date}}')
      AND block_time <  from_iso8601_timestamp('{{end_date}}')
      AND token_bought_mint_address NOT IN (
          'So11111111111111111111111111111111111111112',  -- wSOL
          'EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v', -- USDC
          'Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB'  -- USDT
      )
    GROUP BY 1
    HAVING SUM(amount_usd) BETWEEN {{min_token_volume_usd}} AND {{max_token_volume_usd}}
)
SELECT
    t.block_time,
    t.trader_id,
    t.token_bought_mint_address AS token_mint,
    t.token_bought_symbol       AS token_symbol,
    t.amount_usd,
    t.tx_id
FROM dex_solana.trades t
JOIN token_universe u ON u.token_mint = t.token_bought_mint_address
WHERE t.block_time >= from_iso8601_timestamp('{{start_date}}')
  AND t.block_time <  from_iso8601_timestamp('{{end_date}}')
  AND t.amount_usd >= {{min_trade_usd}}
ORDER BY t.block_time
