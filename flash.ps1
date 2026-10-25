param([Parameter(Mandatory=$true)][string]$Port, [string]$ExpectedMac)
$ErrorActionPreference = 'Stop'
if (!$ExpectedMac) {
    $localConfig = Join-Path $PSScriptRoot 'flash.local.json'
    if (Test-Path -LiteralPath $localConfig) {
        $ExpectedMac = (Get-Content -LiteralPath $localConfig -Raw | ConvertFrom-Json).expected_mac
    }
}
if ($ExpectedMac -notmatch '^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$') {
    throw 'Supply -ExpectedMac with the target board MAC, or configure flash.local.json.'
}
$identity = & python -m esptool --chip esp32s3 --port $Port --after no-reset read-mac 2>&1
if ($LASTEXITCODE -ne 0) { throw ($identity -join "`n") }
if (($identity -join "`n") -notmatch [regex]::Escape($ExpectedMac)) { throw 'Board MAC mismatch; refusing to flash.' }
$bins = Join-Path $PSScriptRoot 'binaries'
& python -m esptool --chip esp32s3 --port $Port --baud 921600 --after watchdog-reset write-flash --flash-mode dio --flash-freq 80m --flash-size 16MB 0x0 (Join-Path $bins 'Switch2Dual.ino.bootloader.bin') 0x8000 (Join-Path $bins 'Switch2Dual.ino.partitions.bin') 0xe000 (Join-Path $bins 'boot_app0.bin') 0x10000 (Join-Path $bins 'Switch2Dual.ino.bin')
if ($LASTEXITCODE -ne 0) { throw 'Flash failed.' }
Write-Host 'Release BOOT, then press RESET/EN once if still in download mode.'
