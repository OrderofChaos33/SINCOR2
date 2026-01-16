const fs = require('fs');
const { Wallet } = require('ethers');
const envPath = `${process.cwd()}/.env`;
const backupPath = `${envPath}.bak`;

if (!fs.existsSync(envPath)) {
  console.error('.env not found; aborting');
  process.exit(1);
}

// Backup existing .env
fs.copyFileSync(envPath, backupPath);

// Generate new private key
const wallet = Wallet.createRandom();
const newKey = wallet.privateKey;

// Replace DEPLOYER_PRIVATE_KEY line in .env, or append if missing
let env = fs.readFileSync(envPath, 'utf8');
if (/^DEPLOYER_PRIVATE_KEY=/m.test(env)) {
  env = env.replace(/^DEPLOYER_PRIVATE_KEY=.*$/m, `DEPLOYER_PRIVATE_KEY=${newKey}`);
} else {
  env += `\nDEPLOYER_PRIVATE_KEY=${newKey}\n`;
}

fs.writeFileSync(envPath, env, { mode: 0o600 });
console.log('✅ New deployer key generated and stored in .env (local only). A backup was saved to .env.bak');
console.log('⚠️ Do NOT commit .env.');
