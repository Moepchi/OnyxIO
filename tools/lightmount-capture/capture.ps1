# OnyxIO - Light Mount Mitschnitt. Nur lesen: schickt selbst nichts an die Tastatur,
# startet nur IO Center mit eingeschaltetem Protokoll und sammelt dessen Logs.
param([switch]$Tasten, [string]$Name = '')  # -Name: eigener Zip-Name; -Tasten: Tasten- und Makro-Zeilen bleiben drin (nur die vorgegebenen Tasten druecken!)
$ErrorActionPreference = 'Stop'
$io = "$env:ProgramFiles\IO Center\IO_Center.exe"
if (!(Test-Path $io)) { Write-Host "IO Center nicht gefunden: $io" -F Red; exit 1 }
$logs = "$env:LOCALAPPDATA\be quiet!\IO Center\logs"
$svc = "$env:ProgramData\be quiet!\bequietIOCenterService\logs"
$work = Join-Path $env:TEMP "onyxio-capture"
Remove-Item $work -Recurse -Force -EA SilentlyContinue
New-Item "$work\raw", "$work\out" -ItemType Directory | Out-Null

# Tastendruecke, Makro-Inhalte und Musiktitel fliegen raus, bevor etwas gezippt wird
$bad = 'Input ?Event|Action ?Fired|Recording|macros|pressed|released|MediaDock|media_dock'
if ($Tasten) { $bad = 'MediaDock|media_dock' }

Write-Host "IO Center wird neu gestartet ..." -F Cyan
Get-Process IO_Center -EA SilentlyContinue | Stop-Process -Force
Start-Sleep 2
$start = Get-Date
$env:QT_LOGGING_RULES = 'qlink*.debug=true;bq_hid*.debug=true'
Start-Process $io

Write-Host ""
Write-Host "Mitschnitt laeuft. Jetzt in IO Center herumklicken (siehe ANLEITUNG.txt)." -F Green
Write-Host "BITTE NICHTS TIPPEN, solange der Mitschnitt laeuft."
Write-Host "Fertig? Dann hier ENTER druecken." -F Yellow
while ($true) {
    # IO Center loescht alte Logs nach wenigen Sekunden, deshalb laufend kopieren
    Get-ChildItem $logs, $svc -Filter *.log -EA SilentlyContinue |
        Where-Object LastWriteTime -ge $start |
        ForEach-Object { Copy-Item $_.FullName "$work\raw" -Force -EA SilentlyContinue }
    if ([Console]::KeyAvailable -and [Console]::ReadKey($true).Key -eq 'Enter') { break }
    Start-Sleep -Milliseconds 700
}

Write-Host "Sammle und filtere ..." -F Cyan
foreach ($f in Get-ChildItem "$work\raw" -Filter *.log) {
    Get-Content $f.FullName | Where-Object { $_ -notmatch $bad } |
        ForEach-Object { $_.Replace($env:USERNAME, 'USER') } |
        Set-Content "$work\out\$($f.Name)" -Encoding UTF8
}
$info = @(
    "IO Center: " + (Get-Item $io).VersionInfo.ProductVersion
    "Windows:   " + [Environment]::OSVersion.VersionString
    ""
    Get-PnpDevice -EA SilentlyContinue | Where-Object InstanceId -match 'VID_373F' |
        Format-Table Status, Class, FriendlyName, InstanceId -AutoSize | Out-String -Width 300
)
$info | Set-Content "$work\out\geraet.txt" -Encoding UTF8

$zip = Join-Path ([Environment]::GetFolderPath('Desktop')) $(if ($Name) { "$Name.zip" } elseif ($Tasten) { "LightMount-Tasten.zip" } else { "LightMount-Mitschnitt.zip" })
Compress-Archive "$work\out\*" $zip -Force
Remove-Item $work -Recurse -Force
Write-Host ""
Write-Host "Fertig: $zip" -F Green
Write-Host "Diese Datei bitte zurueckschicken. IO Center kann normal weiterlaufen."
explorer.exe /select,"$zip"
