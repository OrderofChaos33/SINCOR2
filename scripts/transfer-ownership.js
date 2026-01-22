require('dotenv').config();
const { ethers } = require('ethers');

async function main() {
  const provider = new ethers.JsonRpcProvider(process.env.BASE_RPC_URL || 'https://mainnet.base.org');
  const pk = process.env.DEPLOYER_PRIVATE_KEY;
  if (!pk) {
    console.error('❌ DEPLOYER_PRIVATE_KEY not found in environment. Add it to .env (local only) or export as env var.');
    process.exit(1);
  }

  const wallet = new ethers.Wallet(pk, provider);
  console.log('Using deployer address:', wallet.address);

  // Target new owner (multisig / safe)
  const newOwner = process.env.MULTISIG_ADDRESS || process.env.SAFE_WALLET_ADDRESS;
  if (!newOwner) {
    console.error('❌ No target owner found. Set MULTISIG_ADDRESS or SAFE_WALLET_ADDRESS in .env');
    process.exit(1);
  }

  // Contracts from .env
  const SINC = process.env.SINC_TOKEN_ADDRESS;
  const BONDING = process.env.BONDING_CURVE_ADDRESS;
  const AMM = process.env.AMM_ROUTER_ADDRESS;

  const contracts = [
    { name: 'SINC Token', address: SINC },
    { name: 'Bonding Curve', address: BONDING },
    { name: 'AMM Router', address: AMM },
  ].filter(c => c.address);

  if (contracts.length === 0) {
    console.error('❌ No contract addresses found in .env (SINC_TOKEN_ADDRESS, BONDING_CURVE_ADDRESS, AMM_ROUTER_ADDRESS).');
    process.exit(1);
  }

  for (const c of contracts) {
    console.log(`\n➡️  Processing ${c.name} at ${c.address}`);
    try {
      // Minimal ABI for ownership transfer
      const abi = ['function owner() view returns (address)', 'function transferOwnership(address)'];
      const contract = new ethers.Contract(c.address, abi, wallet);

      const onChainOwner = await contract.owner();
      console.log('   Current owner:', onChainOwner);

      if (onChainOwner.toLowerCase() !== wallet.address.toLowerCase()) {
        console.warn('   ⚠️ The provided key is NOT the owner for this contract. Skipping.');
        continue;
      }

      const tx = await contract.transferOwnership(newOwner);
      console.log('   Sent transferOwnership tx:', tx.hash);
      await tx.wait();
      console.log('   ✅ Ownership transferred to', newOwner);
    } catch (err) {
      console.error('   ❌ Failed:', err.message || err);
    }
  }

  console.log('\nAll done. Verify ownership on Basescan and remove/rotate keys as needed.');
}

main().catch(e => { console.error(e); process.exit(1); });
