SINC Deploy Workspace

This isolated workspace provides a minimal Hardhat setup to compile and deploy the SINC ERC-20 token and seed DEX liquidity without compiling unrelated contracts in the main repo.

Usage
-----
1. cd sinc-deploy
2. npm install
3. Set environment variables (example in .env):
   - BASE_SEPOLIA_RPC_URL
   - DEPLOYER_PRIVATE_KEY
   - UNISWAP_V2_ROUTER (router address for testnet)
4. npm run compile
5. npm run deploy:sinc
6. npm run mint:sinc -- <TOKEN_ADDR> <TO_ADDRESS> <AMOUNT>
7. npm run add-liquidity (see scripts/add_liquidity.js usage)

Market maker
------------
A minimal market-maker script is included (scripts/market_maker.js). Configure the following environment variables and run it with node:
- MARKET_MAKER_PRIVATE_KEY - private key with funds for performing swaps
- UNISWAP_V2_ROUTER - router address
- SINC_ADDRESS - deployed SINC token address
- TOKEN_B_ADDRESS - pair token address (e.g., USDC)
- RPC_URL - JSON-RPC endpoint
- MM_AMOUNT_IN - amount of SINC (or token) to check
- MM_PRICE_THRESHOLD - price threshold to trigger action

Run example (dry-run):
node scripts/market_maker.js


Security
--------
- Never store real private keys in source. Use environment variables and secret managers.
- Test on Base Sepolia before mainnet deployment.
