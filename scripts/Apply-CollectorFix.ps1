# Run this locally to apply the reviewed collector changes outside this workspace.
param(
    [string]$CollectorDirectory = 'C:\myprojects\study\kr_stocks_dashin_collector',
    [ValidateSet('daily_runner.py', 'run_schedule.bat', 'collect_stock_info.py', 'collect_market_index_min.py', 'collect_investor_trend_min.py', 'collect_stock_history.py')]
    [string[]]$Files = @('daily_runner.py', 'run_schedule.bat', 'collect_stock_info.py', 'collect_market_index_min.py', 'collect_investor_trend_min.py', 'collect_stock_history.py')
)
$ErrorActionPreference = 'Stop'
$fixDirectory = Join-Path $PSScriptRoot '..\collector_fix'
$expected = @{
    'daily_runner.py' = '20114D8BD62F3DBC64335EEFAA8B9C08066777CA37A0A4587B4724B4ED2D2FBE'
    'run_schedule.bat' = '38897B256B7B1AD6639301FDE88F1CC8BAA43616A73E0C415CE7068861CF3B0D'
    'collect_stock_info.py' = '13B017AFB5B845BDC2FCF732E8140FDB54C63C5DF15D4A6D9DAABBCBBCF59EAF'
    'collect_market_index_min.py' = 'A90CD2C55BC44FE60C6F7E43C552A1BD61B434660E327FE8CD85B2A337FDF70D'
    'collect_investor_trend_min.py' = @(
        'AD752F9B455DB3B5A9890353C1EFFCC9333CB78A37946CC753551EF2DA8D8BCB',
        '3A6A9B36C9C857F4E92366D9D4A9CAB853D050DF9FC0C7DF049D320B7191A334',
        'FE1FE66256A9592F488E75D6DD45ADBE34D6F07A32472C39A8DB0EB85C94F70E'
    )
    'collect_stock_history.py' = '4B6645DB37A19BB5CE94324934F01E15EA65C8597E3D6435B4389901703F5FAA'
}
foreach ($name in $Files) {
    $destination = Join-Path $CollectorDirectory $name
    $source = Join-Path $fixDirectory $name
    if ((Get-FileHash -LiteralPath $destination).Hash -eq (Get-FileHash -LiteralPath $source).Hash) { continue }
    if ((Get-FileHash -LiteralPath $destination).Hash -notin @($expected[$name])) {
        throw "$name has changed since review. No files were applied."
    }
}
$backupDirectory = Join-Path $CollectorDirectory ('backup-login-fix-' + (Get-Date -Format 'yyyyMMdd-HHmmss-fff'))
New-Item -ItemType Directory -Path $backupDirectory | Out-Null
foreach ($name in $Files) {
    $destination = Join-Path $CollectorDirectory $name
    Copy-Item -LiteralPath $destination -Destination (Join-Path $backupDirectory $name)
    Copy-Item -LiteralPath (Join-Path $fixDirectory $name) -Destination $destination
}
Write-Host "Collector fix applied. Originals backed up in $backupDirectory"
