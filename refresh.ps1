# refresh.ps1 — pull outcomes CSV from VM and run analysis
# Usage: .\refresh.ps1
# Set your GCP variables below

$VM      = "YOUR_INSTANCE_NAME"
$ZONE    = "YOUR_ZONE"           # e.g. us-east1-c
$PROJECT = "YOUR_PROJECT_ID"
$REMOTE_CSV = "/home/YOUR_USER/signal_bot/hounds_outcomes.csv"
$LOCAL_CSV  = "$env:USERPROFILE\hounds_outcomes_fresh.csv"

Write-Host "Pulling CSV from VM..." -ForegroundColor Cyan
gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT `
    --command="cp $REMOTE_CSV /tmp/hounds_snap.csv"
gcloud compute scp "${VM}:/tmp/hounds_snap.csv" $LOCAL_CSV `
    --zone=$ZONE --project=$PROJECT

Write-Host "`nRunning analysis..." -ForegroundColor Cyan
python "$env:USERPROFILE\analyze_hounds.py"
