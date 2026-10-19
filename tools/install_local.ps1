<#
.SYNOPSIS
Install a prepared release only after device work ends and the app closes normally.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Installer,
    [Parameter(Mandatory)][string]$InstallDir,
    [Parameter(Mandatory)][string]$LogPath,
    [int]$WaitSeconds = 3600
)
$ErrorActionPreference = 'Stop'
$setupPath = (Resolve-Path -LiteralPath $Installer).Path
$targetPath = [IO.Path]::GetFullPath($InstallDir)
$targetExe = Join-Path $targetPath 'DDMY X6 Flasher.exe'
$lockPath = Join-Path $env:LOCALAPPDATA 'DDMY-X6-Flasher/private_dumps/device.lock'
$deadline = [DateTime]::UtcNow.AddSeconds($WaitSeconds)

function Get-TargetProcesses {
    $running = @(Get-CimInstance Win32_Process -Filter "Name = 'DDMY X6 Flasher.exe'")
    if ($running | Where-Object { -not $_.ExecutablePath }) {
        throw 'App-Pfad konnte nicht geprüft werden. Keine Installation.'
    }
    @($running | Where-Object { $_.ExecutablePath -eq $targetExe })
}

function Test-DeviceIdle {
    # Take the same byte lock for an instant, without accessing USB or logs.
    if (-not (Test-Path -LiteralPath $lockPath)) {
        throw 'Gerätesperre der laufenden App fehlt. Status unbekannt; keine Installation.'
    }
    $stream = [IO.File]::Open($lockPath, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    try {
        try { $stream.Lock(0, 1) }
        catch [IO.IOException] {
            if (($_.Exception.HResult -band 0xffff) -eq 33) { return $false }
            throw
        }
        $stream.Unlock(0, 1)
        return $true
    }
    finally { $stream.Dispose() }
}

$closeRequested = $false
while ($true) {
    $running = @(Get-TargetProcesses)
    if ($running.Count -eq 0) { break }
    if ([DateTime]::UtcNow -ge $deadline) {
        throw 'App läuft weiterhin. Update wurde nicht installiert; kein Prozess wurde beendet.'
    }
    if (-not $closeRequested -and (Test-DeviceIdle)) {
        # WM_CLOSE reaches the app's own busy/unsaved-edit guards. Never kill it.
        foreach ($item in $running) {
            $process = Get-Process -Id $item.ProcessId -ErrorAction SilentlyContinue
            if ($process -and $process.MainWindowHandle -ne 0) {
                $closeRequested = $process.CloseMainWindow() -or $closeRequested
            }
        }
        if ($closeRequested) { Write-Output 'Auftrag beendet. Reguläres Schließen angefordert.' }
    }
    Start-Sleep -Seconds 3
}
# Setup independently checks all X6 processes again before replacing files.
if ((Test-Path -LiteralPath $lockPath) -and -not (Test-DeviceIdle)) {
    throw 'Ein Geräteauftrag läuft weiterhin. Keine Installation.'
}
$arguments = @('/ALLUSERS', '/VERYSILENT', '/SUPPRESSMSGBOXES', '/SP-', '/NORESTART',
               '/NOCLOSEAPPLICATIONS', '/NORESTARTAPPLICATIONS',
               ('/DIR="{0}"' -f $targetPath), ('/LOG="{0}"' -f [IO.Path]::GetFullPath($LogPath)))
$setup = Start-Process -FilePath $setupPath -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -PassThru -Wait
if ($setup.ExitCode -ne 0) { throw "Installer abgebrochen: Exitcode $($setup.ExitCode). Protokoll: $LogPath" }
Write-Output "Installation abgeschlossen: $targetPath"
