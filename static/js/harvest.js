/**
 * harvest.js — Harvest Moon claim page JS
 * No heavy dependencies. Vanilla fetch + DOM.
 */

'use strict';

// ── Live stats ────────────────────────────────────────────────────────────────
async function loadStats() {
  try {
    const res = await fetch('/api/harvest/status');
    if (!res.ok) return;
    const data = await res.json();
    const claimsEl = document.getElementById('stat-claims');
    if (claimsEl && data.total_claims != null) {
      claimsEl.textContent = Number(data.total_claims).toLocaleString();
    }
    // Update contract address card if deployed
    const contractAddr = document.getElementById('harvest-contract-addr');
    const contractCard = document.getElementById('harvest-contract-card');
    if (data.contract_address && contractAddr && contractCard) {
      contractAddr.textContent = data.contract_address;
      contractCard.classList.remove('proof-card--pending');
      contractCard.href = `https://basescan.org/address/${data.contract_address}`;
    }
  } catch (_) {
    // silently ignore — stats are non-critical
  }
}

// ── Eligibility check ─────────────────────────────────────────────────────────
async function checkEligibility() {
  const input  = document.getElementById('wallet-input');
  const result = document.getElementById('eligibility-result');
  const btn    = document.getElementById('check-btn');

  if (!input || !result) return;

  const address = input.value.trim();
  if (!address) {
    setResult(result, 'error', 'Please enter a wallet address.');
    return;
  }
  if (!isValidAddress(address)) {
    setResult(result, 'error', 'Invalid address format. Must be 0x followed by 40 hex characters.');
    return;
  }

  btn.disabled = true;
  btn.textContent = 'Checking…';
  setResult(result, 'info', 'Checking eligibility…');

  try {
    const res = await fetch(`/api/harvest/eligibility?address=${encodeURIComponent(address)}`);
    const data = await res.json();

    if (data.eligible) {
      setResult(result, 'success',
        `✓ Eligible — ${data.amount || ''} SINC utility access credits available. ` +
        `Claim window opens Sep 26, 2026.`
      );
    } else if (data.already_claimed) {
      setResult(result, 'info', '✓ This address has already claimed.');
    } else {
      setResult(result, 'error',
        'Not eligible in this campaign. ' +
        'Interact with a SINCOR agent skill on Base to qualify for future distributions.'
      );
    }
  } catch (err) {
    setResult(result, 'error', 'Could not check eligibility. Please try again.');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Check';
  }
}

// ── Notify form ───────────────────────────────────────────────────────────────
async function submitNotify(event) {
  event.preventDefault();
  const emailInput = document.getElementById('notify-email');
  const resultEl   = document.getElementById('notify-result');
  if (!emailInput) return;

  const email = emailInput.value.trim();
  if (!email) return;

  try {
    const res = await fetch('/api/waitlist/signup', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, source: 'harvest_notify' }),
    });
    if (res.ok) {
      setResult(resultEl, 'success', '✓ We\'ll notify you when the claim window opens.');
      emailInput.value = '';
    } else {
      setResult(resultEl, 'error', 'Sign-up failed. Please try again.');
    }
  } catch (_) {
    setResult(resultEl, 'error', 'Sign-up failed. Please try again.');
  }
}

// ── Helpers ───────────────────────────────────────────────────────────────────
function setResult(el, type, message) {
  if (!el) return;
  el.className = 'claim-box__result ' + type;
  el.textContent = message;
}

function isValidAddress(addr) {
  return /^0x[0-9a-fA-F]{40}$/.test(addr);
}

// ── Enter key for check ───────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', function () {
  loadStats();

  const input = document.getElementById('wallet-input');
  if (input) {
    input.addEventListener('keydown', function (e) {
      if (e.key === 'Enter') checkEligibility();
    });
  }
});
