<#
.SYNOPSIS
    Adds the Golf Simulator launcher to Startup (runs at login) and the Desktop.
    Run it while signed in as the account that uses the simulator.

    powershell -ExecutionPolicy Bypass -File install.ps1             # install
    powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall  # remove
    powershell -ExecutionPolicy Bypass -File install.ps1 -User Golf  # for another account
#>
param([switch]$Uninstall, [string]$User)

$launcher = Join-Path $PSScriptRoot 'launcher.ps1'
$icon = Join-Path (Split-Path $PSScriptRoot) 'games_golf_22710.ico'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$shell = New-Object -ComObject WScript.Shell

if ($User) {
    $home_ = Join-Path (Split-Path $env:USERPROFILE) $User
    if (-not (Test-Path $home_)) { throw "No profile folder at $home_" }
    $startup = Join-Path $home_ 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'
    $desktop = Join-Path $home_ 'Desktop'
} else {
    $startup = [Environment]::GetFolderPath('Startup')
    $desktop = [Environment]::GetFolderPath('Desktop')
}

foreach ($folder in $startup, $desktop) {
    $path = Join-Path $folder 'Golf Simulator.lnk'
    if ($Uninstall) {
        if (Test-Path $path) { Remove-Item $path; Write-Host "Removed $path" }
        continue
    }
    $lnk = $shell.CreateShortcut($path)
    $lnk.TargetPath = $powershell
    $lnk.Arguments = "-NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$launcher`""
    $lnk.WorkingDirectory = $PSScriptRoot
    $lnk.WindowStyle = 7
    if (Test-Path $icon) { $lnk.IconLocation = $icon }
    $lnk.Save()
    Write-Host "Created $path"
}

# The old batch file would boot GSPro behind the menu if it is still set to run at login.
$old =Get-ChildItem $startup -Filter '*golf*' -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne 'Golf Simulator.lnk' }
if ($old -and -not $Uninstall) {
    Write-Host "`nHeads up - these are also in Startup and may start apps on their own:" -ForegroundColor Yellow
    $old | ForEach-Object { Write-Host "  $($_.FullName)" }
}
