$ErrorActionPreference = 'Stop'
$exe = (Resolve-Path 'outputs/single_file/ProcurementRegistry.exe').Path
$report = Get-Content 'outputs/single_file/build_report.json' -Raw | ConvertFrom-Json
$info = (Get-Item $exe).VersionInfo
if ($info.ProductName -ne 'Procurement Registry') { throw 'Wrong product name' }
if ($info.FileVersion -ne '3.0.0.0') { throw 'Wrong file version' }

function Test-Startup {
    $process = Start-Process $exe -ArgumentList '--self-test' -Wait -PassThru
    if ($process.ExitCode -ne 0) {
        $log = Join-Path $env:LOCALAPPDATA 'ProcurementRegistry/bootstrap_error.log'
        if (Test-Path $log) { Get-Content $log }
        $diagnostic = Join-Path $env:LOCALAPPDATA 'ProcurementRegistry/startup_diagnostics.json'
        if (Test-Path $diagnostic) { Get-Content $diagnostic }
        throw "EXE self-test failed with exit code $($process.ExitCode)"
    }
}
Test-Startup
$cache = Join-Path $env:LOCALAPPDATA "ProcurementRegistry/cache/$($report.cache_id)"
$asset = Join-Path $cache 'app/index.html'
$firstWrite = (Get-Item $asset).LastWriteTimeUtc
Test-Startup
if ((Get-Item $asset).LastWriteTimeUtc -ne $firstWrite) { throw 'Healthy cache was unnecessarily extracted again' }
$expected = (Get-FileHash $asset -Algorithm SHA256).Hash
[IO.File]::WriteAllText($asset, 'deliberately damaged test asset')
Test-Startup
if ((Get-FileHash $asset -Algorithm SHA256).Hash -ne $expected) { throw 'Damaged asset was not repaired' }
$result = Get-Content (Join-Path $env:LOCALAPPDATA 'ProcurementRegistry/startup_diagnostics.json') -Raw | ConvertFrom-Json
if ($result.status -ne 'passed') { throw 'Startup diagnostic did not pass' }
Write-Output 'PASS: native Windows extraction, imports, HTTP, cache reuse and automatic asset repair.'
