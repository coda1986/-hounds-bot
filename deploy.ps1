# deploy.ps1 — deploy + verify standard for main bot file
# Usage: .\deploy.ps1
# Set your GCP variables below

$VM      = "YOUR_INSTANCE_NAME"
$ZONE    = "YOUR_ZONE"           # e.g. us-east1-c
$PROJECT = "YOUR_PROJECT_ID"
$SERVICE = "YOUR_SERVICE_NAME"   # e.g. my-bot.service
$LOCAL   = "$env:USERPROFILE\cooking_paper.py"
$REMOTE  = "/home/YOUR_USER/signal_bot/cooking_paper.py"

# 1. Syntax check before upload
Write-Host "`n[1/4] Checking local syntax..." -ForegroundColor Cyan
$syntax = python -c "import ast; ast.parse(open('$LOCAL',encoding='utf-8').read()); print('OK')" 2>&1
if ($syntax -ne "OK") { Write-Host "SYNTAX ERROR: $syntax" -ForegroundColor Red; exit 1 }
Write-Host "     OK" -ForegroundColor Green

# 2. Upload
Write-Host "[2/4] Uploading to VM..." -ForegroundColor Cyan
gcloud compute scp "$LOCAL" "${VM}:${REMOTE}" --zone=$ZONE --project=$PROJECT

# 3. Verify byte count
Write-Host "[3/4] Verifying upload..." -ForegroundColor Cyan
$local_size  = (Get-Item $LOCAL).Length
$remote_size = gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT `
    --command="wc -c < $REMOTE" 2>&1
$remote_size = $remote_size.Trim()
if ($local_size -ne [int]$remote_size) {
    Write-Host "MISMATCH: local=$local_size VM=$remote_size" -ForegroundColor Red; exit 1
}
Write-Host "     OK ($local_size bytes)" -ForegroundColor Green

# 4. Restart and check logs
Write-Host "[4/4] Restarting service..." -ForegroundColor Cyan
gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT `
    --command="sudo systemctl restart $SERVICE && sleep 3 && sudo journalctl -u $SERVICE --no-pager -n 6"

Write-Host "`nDeploy complete." -ForegroundColor Green
