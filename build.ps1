$ErrorActionPreference = 'Stop'
$cli = Join-Path $env:LOCALAPPDATA 'Programs\Arduino IDE\resources\app\lib\backend\resources\arduino-cli.exe'
if (!(Test-Path -LiteralPath $cli)) { throw 'Arduino CLI / Arduino IDE is required.' }
$workRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot 'work'))
$buildRoot = Join-Path $workRoot ('build-' + [guid]::NewGuid().ToString('N'))
$sketchPath = Join-Path $buildRoot 'sketch\Switch2Dual'
$compilePath = Join-Path $buildRoot 'compile'
$exportPath = Join-Path $buildRoot 'export'
$bins = Join-Path $PSScriptRoot 'binaries'
New-Item -ItemType Directory -Force -Path $exportPath,$sketchPath,$bins | Out-Null
try {
    # Some CLI versions also export into the sketch; keep all output temporary.
    Get-ChildItem -LiteralPath (Join-Path $PSScriptRoot 'firmware\Switch2Dual') -Force |
        Where-Object { $_.Name -ne 'build' } |
        ForEach-Object { Copy-Item -LiteralPath $_.FullName -Destination $sketchPath -Recurse -Force }
    & $cli compile --export-binaries=false --fqbn 'esp32:esp32:esp32s3:USBMode=default,CDCOnBoot=default,MSCOnBoot=default,DFUOnBoot=default,FlashSize=16M,FlashMode=dio,PSRAM=disabled,PartitionScheme=default' --build-path $compilePath --output-dir $exportPath $sketchPath
    if ($LASTEXITCODE -ne 0) { throw 'Build failed.' }
    foreach ($name in @('Switch2Dual.ino.bin','Switch2Dual.ino.bootloader.bin','Switch2Dual.ino.partitions.bin')) {
        Copy-Item -LiteralPath (Join-Path $exportPath $name) -Destination (Join-Path $bins $name) -Force
    }
    Copy-Item -LiteralPath (Join-Path $env:LOCALAPPDATA 'Arduino15\packages\esp32\hardware\esp32\3.3.7\tools\partitions\boot_app0.bin') -Destination (Join-Path $bins 'boot_app0.bin') -Force
}
finally {
    $resolvedBuild = [System.IO.Path]::GetFullPath($buildRoot)
    $allowedPrefix = $workRoot.TrimEnd('\') + '\'
    if (!$resolvedBuild.StartsWith($allowedPrefix,[System.StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe build cleanup path.' }
    if (Test-Path -LiteralPath $resolvedBuild) { Remove-Item -LiteralPath $resolvedBuild -Recurse -Force }
    if ((Test-Path -LiteralPath $workRoot) -and !(Get-ChildItem -LiteralPath $workRoot -Force | Select-Object -First 1)) {
        Remove-Item -LiteralPath $workRoot -Force
    }
}
