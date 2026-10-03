# run_tests.ps1 - the Windows Sandbox test run for the Manticore installer (Round 29, SONNET_SPEC_R29 section 7).
# Runs by itself inside a clean Windows with nobody at the keyboard. Every step writes a line to steps.txt; the last thing it does
# is write verdict.txt (PASS or FAIL, with the reasons), which is what the person waiting on the host looks for.
#
# UNVERIFIED: Sonnet could not run Windows Sandbox. If a step fails for a reason that looks like THIS SCRIPT's fault (not the
# program's), send Claude steps.txt.

$ErrorActionPreference = 'Continue'
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$res = Join-Path 'C:\sandbox_results' $stamp
New-Item -ItemType Directory -Force -Path $res | Out-Null
$stepsFile = Join-Path $res 'steps.txt'
$script:fails = @()
$script:notClean = $false

function Log([string]$msg) {
    $line = '[{0}] {1}' -f (Get-Date -Format 'HH:mm:ss'), $msg
    Add-Content -Path $stepsFile -Value $line -Encoding UTF8
    Write-Host $line
}
function Fail([string]$msg) { $script:fails += $msg; Log ('FAIL: ' + $msg) }
function Ok([string]$msg) { Log ('ok: ' + $msg) }

function Get-JreJava {
    # java.exe processes that were started from the program's own jre\ folder
    @(Get-CimInstance Win32_Process -Filter "Name='java.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.ExecutablePath -like '*\Manticore\jre\*' })
}
function Stop-JreJava { Get-JreJava | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue } }

function Wait-Gone([string]$path, [int]$seconds) {
    # Inno's uninstaller hands over to a copy of itself, so Start-Process -Wait returns early: poll for the folder to vanish
    for ($i = 0; $i -lt $seconds; $i++) {
        if (-not (Test-Path $path)) { return $true }
        Start-Sleep -Seconds 1
    }
    return (-not (Test-Path $path))
}

function Install-Silent([string]$installer, [string]$logName) {
    $p = Start-Process -FilePath $installer -ArgumentList '/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/LOG=$res\$logName" -Wait -PassThru
    return $p.ExitCode
}
function Uninstall-Silent([string]$unins, [bool]$purge) {
    $argList = @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART')
    if ($purge) { $argList += '/PURGE' }
    Start-Process -FilePath $unins -ArgumentList $argList -Wait | Out-Null
}

$app = Join-Path $env:LOCALAPPDATA 'Programs\Manticore'
$cli = Join-Path $app 'Manticore-cli.exe'
$shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Manticore.lnk'
$userData = Join-Path $env:APPDATA 'Manticore'
$localData = Join-Path $env:LOCALAPPDATA 'Manticore'
$forgeData = Join-Path $env:APPDATA 'Forge'

try {
    $ramMb = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1MB)
    Log ("Sandbox: {0} MB of memory, {1}" -f $ramMb, (Get-CimInstance Win32_OperatingSystem).Caption)

    # 1. clean start
    foreach ($tool in 'java', 'python') {
        if (Get-Command $tool -ErrorAction SilentlyContinue) { $script:notClean = $true; Log ("NOT CLEAN: $tool was found on PATH") }
        else { Ok "$tool is not installed (a clean Windows)" }
    }

    # 2. silent install
    $installerFile = Get-ChildItem 'C:\installer_in' -Filter 'Manticore-*-setup.exe' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $installerFile) { throw 'no Manticore-*-setup.exe in C:\installer_in' }
    Log ("installer: {0} ({1:N0} MB)" -f $installerFile.Name, ($installerFile.Length / 1MB))
    $localInstaller = Join-Path $env:TEMP $installerFile.Name
    Copy-Item $installerFile.FullName $localInstaller
    $code = Install-Silent $localInstaller 'install.log'
    if ($code -ne 0) { Fail "installer exited with $code" } else { Ok 'silent install finished (exit 0)' }
    if (Test-Path (Join-Path $app 'Manticore.exe')) { Ok 'program folder and Manticore.exe exist' } else { Fail "Manticore.exe is missing from $app" }
    if (Test-Path $shortcut) { Ok 'Start-menu shortcut exists' } else { Fail "Start-menu shortcut is missing ($shortcut)" }
    if (-not (Test-Path $cli)) { throw 'Manticore-cli.exe is missing: cannot go on' }

    # 3. --version
    & $cli --version 2>&1 | Out-File -FilePath (Join-Path $res 'version.txt') -Encoding utf8
    if ($LASTEXITCODE -ne 0) { Fail "--version exited with $LASTEXITCODE" } else { Ok '--version ran' }
    $versionText = Get-Content (Join-Path $res 'version.txt') -Raw
    if ($versionText -match 'code da39a3ee' -or $versionText -match 'code unknown') { Fail '--version does not know its own code' }

    # 4. --soak 10 (the canary, then ten games)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    $p = Start-Process -FilePath $cli -ArgumentList '--soak', '10' -PassThru -Wait -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $res 'soak_stdout.txt') -RedirectStandardError (Join-Path $res 'soak_stderr.txt')
    Log ("--soak 10 finished in {0:N0} minutes, exit code {1}" -f $sw.Elapsed.TotalMinutes, $p.ExitCode)
    $soakOut = Get-Content (Join-Path $res 'soak_stdout.txt') -Raw -ErrorAction SilentlyContinue
    if ($soakOut -match 'canary: OK') { Ok 'the canary was caught' } else { Fail 'the canary did not report OK' }
    $summary = Get-ChildItem (Join-Path $localData 'soak') -Recurse -Filter 'soak_summary.txt' -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($summary) {
        Copy-Item $summary.FullName (Join-Path $res 'soak_summary.txt')
        Copy-Item $summary.Directory.FullName (Join-Path $res 'soak_run') -Recurse -ErrorAction SilentlyContinue
        $first = (Get-Content $summary.FullName -TotalCount 1)
        if ($first -eq 'SOAK RUN: VALID') { Ok 'the soak run is VALID' } else { Fail ("the soak summary says: $first") }
    } else { Fail 'no soak_summary.txt was written' }
    if ($p.ExitCode -ne 0) { Fail ("--soak 10 exited with {0}" -f $p.ExitCode) }

    # 5. the kill tests (alpha must-have 1): no java.exe may outlive the program. Timed from the moment the bundled java.exe
    #    appears, so the test is never empty: 3 s later Forge is still starting up, 60 s later a game is under way.
    foreach ($delay in 3, 60) {
        Stop-JreJava
        $p = Start-Process -FilePath $cli -ArgumentList '--soak', '1' -PassThru -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $res "kill${delay}_stdout.txt") -RedirectStandardError (Join-Path $res "kill${delay}_stderr.txt")
        $appeared = $false
        for ($i = 0; $i -lt 90; $i++) {
            if ((Get-JreJava).Count -gt 0) { $appeared = $true; break }
            if ($p.HasExited) { break }
            Start-Sleep -Seconds 1
        }
        if (-not $appeared) { Fail "kill test ${delay}s: the bundled java.exe never started"; if (-not $p.HasExited) { Stop-Process -Id $p.Id -Force }; continue }
        Start-Sleep -Seconds $delay
        if ($p.HasExited) { Fail "kill test ${delay}s: the program had already exited before it could be killed"; Stop-JreJava; continue }
        & taskkill.exe /F /PID $p.Id 2>&1 | Out-Null
        Start-Sleep -Seconds 5
        $left = Get-JreJava
        if ($left.Count -gt 0) { Fail ("kill test {0}s: {1} java.exe from the bundled jre\ was still running 5 s after the program was killed" -f $delay, $left.Count); Stop-JreJava }
        else { Ok "kill test ${delay}s: no java.exe left 5 s after the program was killed" }
    }

    # 6. what the program made in the per-user folders (and Forge's own), BEFORE the markers below go in
    $lines = @()
    foreach ($folder in $userData, $localData, $forgeData, (Join-Path $env:USERPROFILE '.forge')) {
        if (Test-Path $folder) {
            $files = @(Get-ChildItem $folder -Recurse -Force -File -ErrorAction SilentlyContinue)
            $lines += ("== {0}  ({1} files, {2:N1} MB)" -f $folder, $files.Count, (($files | Measure-Object Length -Sum).Sum / 1MB))
            $lines += ($files | Select-Object -First 200 | ForEach-Object { "   {0}  {1}" -f $_.FullName.Substring($folder.Length), $_.Length })
        } else { $lines += "== $folder  (does not exist)" }
    }
    $lines | Out-File -FilePath (Join-Path $res 'data_folders.txt') -Encoding utf8
    Ok 'listed the per-user folders (data_folders.txt)'
    # markers: proof that a data folder survives (or does not survive) an uninstall
    foreach ($m in (Join-Path $userData 'my_decks'), $localData, $forgeData) { New-Item -ItemType Directory -Force -Path $m | Out-Null }
    'sandbox marker' | Out-File (Join-Path $userData 'my_decks\sandbox_marker.txt')
    'sandbox marker' | Out-File (Join-Path $localData 'sandbox_marker.txt')
    'sandbox marker (stands for a real Forge install)' | Out-File (Join-Path $forgeData 'sandbox_marker.txt')

    # 7. silent uninstall WITHOUT /PURGE: the program goes, the tester's data stays
    Stop-JreJava
    Uninstall-Silent (Join-Path $app 'unins000.exe') $false
    if (Wait-Gone $app 120) { Ok 'uninstall (no /PURGE): the program folder is gone' } else { Fail "uninstall (no /PURGE): $app is still there after 120 s" }
    if (Test-Path $shortcut) { Fail 'uninstall (no /PURGE): the Start-menu shortcut is still there' } else { Ok 'uninstall (no /PURGE): the Start-menu shortcut is gone' }
    if ((Test-Path (Join-Path $userData 'my_decks\sandbox_marker.txt')) -and (Test-Path (Join-Path $localData 'sandbox_marker.txt'))) { Ok 'uninstall (no /PURGE): the data folders were kept' }
    else { Fail 'uninstall (no /PURGE): the per-user data folders were deleted' }
    if (@(Get-JreJava).Count -gt 0) { Fail 'java.exe from the jre is running after the uninstall'; Stop-JreJava }

    # 8. reinstall, then silent uninstall WITH /PURGE: now the data goes too, but Forge's own folder never does
    $code = Install-Silent $localInstaller 'reinstall.log'
    if ($code -ne 0) { Fail "reinstall exited with $code" } else { Ok 'reinstall finished (exit 0)' }
    Uninstall-Silent (Join-Path $app 'unins000.exe') $true
    if (Wait-Gone $app 120) { Ok 'uninstall /PURGE: the program folder is gone' } else { Fail "uninstall /PURGE: $app is still there after 120 s" }
    Start-Sleep -Seconds 5
    if ((Test-Path $userData) -or (Test-Path $localData)) { Fail 'uninstall /PURGE: a Manticore data folder is still there' } else { Ok 'uninstall /PURGE: both Manticore data folders are gone' }
    if (Test-Path (Join-Path $forgeData 'sandbox_marker.txt')) { Ok 'uninstall /PURGE: %APPDATA%\Forge was left alone' } else { Fail 'uninstall /PURGE: %APPDATA%\Forge was touched' }
}
catch {
    Fail ("the test script stopped: " + $_.Exception.Message)
}
finally {
    try {
        $threats = @(Get-MpThreatDetection -ErrorAction Stop)
        if ($threats.Count -gt 0) { Fail ("Windows Defender reported {0} detection(s): see defender.txt" -f $threats.Count); $threats | Out-File (Join-Path $res 'defender.txt') -Encoding utf8 }
        else { Ok 'Windows Defender reported nothing (the false-positive check)' }
    } catch { Log 'could not read the Defender detection history' }
    $verdict = @()
    $verdict += $(if ($script:fails.Count -eq 0) { 'PASS' } else { 'FAIL' })
    $verdict += ("Sandbox memory: {0} MB" -f $ramMb)
    $verdict += $(if ($script:notClean) { 'CLEAN START: no - java or python was already installed, so the run is NOT CLEAN' } else { 'CLEAN START: yes' })
    foreach ($f in $script:fails) { $verdict += ('  - ' + $f) }
    $verdict | Out-File -FilePath (Join-Path $res 'verdict.txt') -Encoding utf8
    Log ('verdict: ' + $verdict[0])
}
