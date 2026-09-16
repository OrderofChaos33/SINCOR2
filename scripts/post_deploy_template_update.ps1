# Post-deploy token canon update — one-shot script
# Run AFTER mainnet deploys complete + after deployments/8453.json has real addresses
# Replaces legacy /sinc placeholders with canonical live token copy and official buy links

$ErrorActionPreference = "Stop"

$root = "C:\Users\cjay4\OneDrive\Desktop\sincor-clean"
$deployPath = "$root\onchain\deployments\8453.json"
$gatewayPath = "$root\templates\sinc_gateway.html"

if (-not (Test-Path $deployPath)) {
    Write-Host "ERROR: $deployPath not found. Run mainnet deploys first."
    exit 1
}

$deploy = Get-Content $deployPath -Raw | ConvertFrom-Json

if (-not $deploy.nft -or $deploy.nft -eq "0x0" -or
    -not $deploy.hook -or $deploy.hook -eq "0x0") {
    Write-Host "ERROR: deployments/8453.json has zero/missing addresses:"
    Write-Host "  nft:   $($deploy.nft)"
    Write-Host "  hook:  $($deploy.hook)"
    Write-Host "Run mainnet deploys first."
    exit 1
}

Write-Host "Live mainnet addresses:"
Write-Host "  NFT:   $($deploy.nft)"
Write-Host "  Hook:  $($deploy.hook)"
Write-Host ""

# Backup current template
$backupPath = "$gatewayPath.bak.$(Get-Date -Format 'yyyyMMdd-HHmmss')"
Copy-Item $gatewayPath $backupPath
Write-Host "Backup saved: $backupPath"

$html = Get-Content $gatewayPath -Raw

# 1. Replace the pre-launch banner under hero with canonical live buy CTA
$oldBanner = 'SINC is mid-relaunch. The legacy v2 pool on Aerodrome has been retired; the canonical SINC contract is verified on Base and the new Uniswap V4 pool launches once Sepolia validation + CertiK Skynet scans complete. No live buy link until the V4 pool is live.'
$newBanner = "SINC is live on Base at the canonical address <span class='mono'>0xe1D836087F6573b665d25CE088793E916D7892f8</span>. Official buy path: <a href='https://getsincor.com/buy' target='_blank' style='color:var(--cyan);'>getsincor.com/buy</a>. Token canon: <a href='https://getsincor.com/token' target='_blank' style='color:var(--cyan);'>/token</a>"
$html = $html.Replace($oldBanner, $newBanner)

# 2. Replace the "View Phase 1 Launch Plan" CTA with Buy SINC
$oldCta = '<a href="#relaunch-status" class="btn-primary">' + "`n" + '        ⚡ View Phase 1 Launch Plan' + "`n" + '      </a>'
$newCta = '<a href="#buy-sinc" class="btn-primary">' + "`n" + '        🪙 Buy SINC' + "`n" + '      </a>'
$html = $html.Replace($oldCta, $newCta)

# 3. Update legacy launch proof line
$oldTests = '32/32 tests passing, awaiting Sepolia validation'
$newTests = "Canonical SINC live address + official buy path active. <a href='https://getsincor.com/token' target='_blank' style='color:var(--cyan);'>Token canon</a> / <a href='https://basescan.org/address/$($deploy.nft)' target='_blank' style='color:var(--cyan);'>NFT</a> / <a href='https://basescan.org/address/$($deploy.hook)' target='_blank' style='color:var(--cyan);'>Hook</a>"
$html = $html.Replace($oldTests, $newTests)

# 4. Update the status block
$oldStatus = '<strong style="color:var(--cyan);">Status:</strong> awaiting Base Sepolia deploy'
$newStatus = "<strong style='color:var(--cyan);'>Status:</strong> Canonical live token surface active. Use <span class='mono'>0xe1D836087F6573b665d25CE088793E916D7892f8</span>, buy at <a href='https://getsincor.com/buy' target='_blank' style='color:var(--cyan);'>/buy</a>, and reference <a href='https://getsincor.com/token' target='_blank' style='color:var(--cyan);'>/token</a>. Hook at <span class='mono'>$($deploy.hook)</span>. Soulbound Genesis NFT at <span class='mono'>$($deploy.nft)</span>."
$html = $html.Replace($oldStatus, $newStatus)

# 5. Update "Pre-launch" placeholders in JS
$html = $html.Replace("if (priceEl) priceEl.textContent = 'Pre-launch';", "if (priceEl) priceEl.textContent = 'Live on V4';")
$html = $html.Replace("if (liqEl) liqEl.textContent = 'Pre-launch';", "if (liqEl) liqEl.textContent = 'Canonical live token';")

# 6. Add a Buy CTA section anchor (#buy-sinc) — insert before the closing </main> or </body>
# For simplicity, we keep #relaunch-status as the buy section now that it shows the live canonical token surface

Set-Content -Path $gatewayPath -Value $html -NoNewline

Write-Host ""
Write-Host "================================"
Write-Host " /sinc page updated for token canon lock"
Write-Host "================================"
Write-Host ""
Write-Host "Next:"
Write-Host "  1. Restart Flask (if running) so template reloads"
Write-Host "  2. Visit https://getsincor.com/sinc and verify:"
Write-Host "     - Banner shows canonical SINC address + /buy link"
Write-Host "     - Buy SINC CTA visible"
Write-Host "     - Live token address matches /token"
Write-Host "  3. If anything looks wrong, restore from backup:"
Write-Host "       Copy-Item '$backupPath' '$gatewayPath'"
