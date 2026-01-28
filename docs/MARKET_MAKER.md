SINC Market Maker & Trading Notes

Goal
----
Enable SINC token to be tradable and to provide initial liquidity/buy pressure so that SINC <-> other tokens have a market.

Quick steps (MVP)
-----------------
1. Deploy SINC token (see SINC_DEPLOY.md)
2. Mint an initial supply to the treasury address
3. Seed liquidity on a UniswapV2-compatible pool (SINC/USDC for example)
4. Run a simple market maker that:
   - Places small buys when price drops below target
   - Swaps small USDC->SINC to create buy pressure
   - Rotates a small budget to avoid high exposure

Simple market-maker strategy
----------------------------
- Monitor pair reserves and calculate price mid-point.
- If price below buy threshold, perform a small swap on the router (swapExactTokensForTokens)
- Optionally add or remove liquidity periodically to tighten spreads

Safety & Budget
---------------
- Start with limited budget on testnet.
- Use a multisig for the treasury on mainnet.
- Implement slippage, gas price bounds and circuit-breakers in the bot.

Next steps I can implement for you
---------------------------------
- A minimal Python/JS market-maker that polls on-chain prices and executes small buy swaps when conditions are met.
- A wallet funding script and a basic dashboard showing LP TVL and buy/sell volume.

If you want, I can start by building the minimal bot that executes USD->SINC buys at configurable thresholds on Base Sepolia.