# kotte_refresh.ps1 — puxa kotte_outcomes.csv do VM e roda backtester
$VM    = "instance-20261005-212837"
$ZONE  = "us-east1-c"
$PROJ  = "cripto-bot-503322"
$REMOTE = "${VM}:/home/abib1/signal_bot/kotte_outcomes.csv"
$LOCAL  = "C:\Users\abib1\kotte_outcomes_fresh.csv"

Write-Host "Puxando kotte_outcomes.csv do VM..."
gcloud compute scp "${REMOTE}" "$LOCAL" --zone=$ZONE --project=$PROJ

if ($LASTEXITCODE -ne 0) { Write-Host "[erro] SCP falhou"; exit 1 }

$n = (Get-Content $LOCAL | Measure-Object -Line).Lines - 1
Write-Host "Linhas no CSV: $n"

python C:\Users\abib1\kotte_backtester.py $LOCAL
