# Post-deploy smoke test — run after Railway redeploy
$urls = @(
  "https://getsincor.com/tokenlists/sincor.tokenlist.json",
  "https://getsincor.com/.well-known/sinc-token.json",
  "https://getsincor.com/static/tokenlists/assets/logo-256.png",
  "https://getsincor.com/static/sincor_og.jpg"
)
foreach ($u in $urls) {
  try {
    $r = Invoke-WebRequest -Uri $u -Method Head -UseBasicParsing -TimeoutSec 20
    Write-Host "OK $($r.StatusCode) $u"
  } catch {
    Write-Host "FAIL $u — $($_.Exception.Message)"
  }
}