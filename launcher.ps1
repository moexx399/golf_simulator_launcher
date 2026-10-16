#Requires -Version 5.1
<#
.SYNOPSIS
    Golf simulator launcher: a full-screen menu for choosing which simulator to boot.

.DESCRIPTION
    Reads launcher.json (next to this script), shows one tile per simulator and,
    when one is picked, runs that simulator's start-up steps with live progress,
    then closes itself.

    Keys: 1-9 pick a tile, arrows + Enter also work, Esc exits to the desktop.

    Step types (used in launcher.json):
      kill             names: [process names without .exe], graceful: true asks apps to close first
      killCommandLine  pattern: wildcard matched against the process command line
      start            path, args, workingDir, wait (until it exits), timeout,
                       minimized, skipIfRunning
      waitPort         port, timeout   - waits until something listens on localhost:port
      waitProcess      name, timeout   - waits until a process with that name exists
      delay            seconds
      focus            process, settle - brings that app's window to the front (after waiting settle s)
      include        set: name of a list in "stepSets"
      clickButton      process, text, timeout - presses a normal Windows button by its caption
                       (e.g. "Launch GSPro"); ifCommandLine skips it unless the process
                       was started with matching arguments; doneIfProcess skips it once
                       that process is running; doneIfWindowClass skips it once the app
                       shows a window of that class
      clickText        process, text, timeout, settle, parkMouse - reads the app's window with
                       Windows text recognition and clicks the words (for game menus)
    Every step accepts "label" (text shown on screen) and "optional": true
    (a failure shows a warning instead of stopping the boot).

    A sim can also list "actions": extra buttons shown under its tile (e.g. GSPro's
    Driving Range), each with its own "label", optional "key" and "steps".

.PARAMETER Select
    Boot this sim id immediately, skipping the menu (e.g. -Select gspro,
    or -Select gspro:range for one of its actions).
.PARAMETER Windowed
    Run in a normal window instead of full screen (for testing).
.PARAMETER DryRun
    Walk through the steps without starting or killing anything.
#>
param(
    [string]$Config = (Join-Path $PSScriptRoot 'launcher.json'),
    [string]$Select,
    [switch]$Windowed,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Xaml

# Window and mouse helpers for the click-through steps. DPI awareness keeps
# window positions in real pixels on the 4K screen.
$nativeSource = @'
using System;
using System.Runtime.InteropServices;
public static class GolfNative {
    [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr hWnd, out RECT rect);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr hWnd);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern void mouse_event(uint flags, uint dx, uint dy, uint data, UIntPtr extra);
    [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr hWnd, int cmd);
    // Windows only lets the app that got the last input take focus; a quick Alt tap counts as that.
    public static bool Focus(IntPtr hWnd) {
        if (IsIconic(hWnd)) ShowWindow(hWnd, 9);
        if (SetForegroundWindow(hWnd) && GetForegroundWindow() == hWnd) return true;
        keybd_event(0x12, 0, 0, UIntPtr.Zero);
        keybd_event(0x12, 0, 2, UIntPtr.Zero);
        SetForegroundWindow(hWnd);
        return GetForegroundWindow() == hWnd;
    }
    public static void Click(int x, int y) {
        // Hover first: Unity UI only takes a click on a button the pointer has entered.
        SetCursorPos(x - 6, y);
        System.Threading.Thread.Sleep(60);
        SetCursorPos(x, y);
        System.Threading.Thread.Sleep(150);
        mouse_event(0x0002, 0, 0, 0, UIntPtr.Zero);
        System.Threading.Thread.Sleep(80);
        mouse_event(0x0004, 0, 0, 0, UIntPtr.Zero);
    }
}
'@
# Compiling C# starts the compiler, which is slow right after sign-in, so the
# helper is built once into a DLL (named after a hash of the code) and reused.
if (-not ('GolfNative' -as [type])) {
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        $hash = -join ($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($nativeSource))[0..5] | ForEach-Object { $_.ToString('x2') })
        $cacheDir = Join-Path $env:LOCALAPPDATA 'GolfSimLauncher'
        $dll = Join-Path $cacheDir "GolfNative-$hash.dll"
        if (-not (Test-Path $dll)) {
            New-Item -ItemType Directory -Force $cacheDir | Out-Null
            Get-ChildItem $cacheDir -Filter 'GolfNative-*.dll' -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue
            Add-Type -TypeDefinition $nativeSource -OutputAssembly $dll -OutputType Library
        }
        if (-not ('GolfNative' -as [type])) { Add-Type -Path $dll }
    } catch {
        if (-not ('GolfNative' -as [type])) { Add-Type -TypeDefinition $nativeSource }
    }
}
[void][GolfNative]::SetProcessDPIAware()

# Only one launcher at a time. Opening it again (desktop shortcut, Startup)
# brings the running one back from the taskbar instead.
$mutex = New-Object System.Threading.Mutex($false, 'Local\GolfSimLauncher')
if (-not $mutex.WaitOne(0)) {
    Get-Process powershell -ErrorAction SilentlyContinue |
        Where-Object { $_.Id -ne $PID -and $_.MainWindowTitle -eq 'Golf Simulator' } |
        Select-Object -First 1 |
        ForEach-Object { [void][GolfNative]::Focus($_.MainWindowHandle) }
    exit
}

# --- helpers -----------------------------------------------------------------

$LogFile = Join-Path $PSScriptRoot 'launcher.log'
if ((Test-Path $LogFile) -and (Get-Item $LogFile).Length -gt 1MB) { Remove-Item $LogFile -Force }

function Write-Log([string]$Message) {
    try { Add-Content -Path $LogFile -Value ('{0:yyyy-MM-dd HH:mm:ss}  {1}' -f (Get-Date), $Message) -Encoding UTF8 } catch { }
}

function Get-Opt($Object, [string]$Name, $Default = $null) {
    if ($null -ne $Object -and $Object.PSObject.Properties[$Name]) { return $Object.$Name }
    return $Default
}

function Expand-Path([string]$Path) {
    if (-not $Path) { return $Path }
    $Path = [Environment]::ExpandEnvironmentVariables($Path)
    # Relative paths (e.g. a logo next to the script) are relative to the launcher folder.
    if (-not [IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $PSScriptRoot $Path }
    return $Path
}

function New-Brush([string]$Color) {
    return [System.Windows.Media.BrushConverter]::new().ConvertFromString($Color)
}

function ConvertTo-XmlText([string]$Text) {
    if ($null -eq $Text) { return '' }
    return [System.Security.SecurityElement]::Escape($Text)
}

function Show-Fatal([string]$Message) {
    Write-Log "FATAL $Message"
    [void][System.Windows.MessageBox]::Show($Message, 'Golf Simulator Launcher', 'OK', 'Error')
}

# Lets the window repaint while a step is running on the UI thread.
function Invoke-Pump {
    $window.Dispatcher.Invoke([Action] { }, [System.Windows.Threading.DispatcherPriority]::Background)
}

function Wait-Until([scriptblock]$Condition, [double]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        if (& $Condition) { return $true }
        Invoke-Pump
        Start-Sleep -Milliseconds 100
    }
    return [bool](& $Condition)
}

function Wait-Seconds([double]$Seconds) { $null = Wait-Until { $false } $Seconds }

function Test-Port([int]$Port) {
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $async = $client.BeginConnect('127.0.0.1', $Port, $null, $null)
        return ($async.AsyncWaitHandle.WaitOne(250) -and $client.Connected)
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

# --- config ------------------------------------------------------------------

# When the launcher starts at sign-in, Windows holds Startup apps back for a
# while; turn that delay off for this account (takes effect next sign-in).
# install.ps1 -Uninstall puts it back.
if (Test-Path (Join-Path ([Environment]::GetFolderPath('Startup')) 'Golf Simulator.lnk')) {
    try {
        $serialize = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Serialize'
        $current = Get-ItemProperty $serialize -ErrorAction SilentlyContinue
        if (-not $current -or $current.StartupDelayInMSec -ne 0 -or $current.WaitForIdleState -ne 0) {
            if (-not (Test-Path $serialize)) { New-Item $serialize | Out-Null }
            New-ItemProperty $serialize -Name StartupDelayInMSec -Value 0 -PropertyType DWord -Force | Out-Null
            New-ItemProperty $serialize -Name WaitForIdleState -Value 0 -PropertyType DWord -Force | Out-Null
            Write-Log 'Turned off the Windows startup-app delay for this account'
        }
    } catch { }
}

# First run from a fresh copy of the repo: start from the example config.
$example = Join-Path $PSScriptRoot 'launcher.example.json'
if (-not (Test-Path $Config) -and (Test-Path $example) -and -not $PSBoundParameters.ContainsKey('Config')) {
    Copy-Item $example $Config
    Write-Log "Created $Config from launcher.example.json"
}

try {
    $cfg = Get-Content -Raw -Encoding UTF8 $Config | ConvertFrom-Json
} catch {
    Show-Fatal "Couldn't read $Config`n`n$($_.Exception.Message)"
    exit 1
}

function Resolve-Steps($Steps) {
    foreach ($step in @($Steps)) {
        if ($null -eq $step) { continue }
        if ($step.type -eq 'include') {
            $set = Get-Opt $cfg.stepSets $step.set
            if ($null -eq $set) { throw "Unknown step set '$($step.set)' in launcher.json" }
            Resolve-Steps $set
        } else {
            $step
        }
    }
}

function Get-SimState($Sim) {
    if (-not (Get-Opt $Sim 'enabled' $true)) {
        return @{ Ok = $false; Text = (Get-Opt $Sim 'status' 'Coming soon') }
    }
    $check = Expand-Path (Get-Opt $Sim 'check')
    if ($check -and -not (Test-Path $check)) {
        return @{ Ok = $false; Text = 'Not installed - set its path in launcher.json' }
    }
    return @{ Ok = $true; Text = (Get-Opt $Sim 'status' '') }
}

# --- click-through automation ------------------------------------------------

# Only this sign-in's processes: with fast user switching, another account's
# copy of the same app must not be closed (or waited on, or clicked).
$script:sessionId = (Get-Process -Id $PID).SessionId
function Get-SessionProcess {
    [CmdletBinding()] param([string]$Name)
    Get-Process -Name $Name -ErrorAction SilentlyContinue | Where-Object { $_.SessionId -eq $script:sessionId }
}

function Get-AppWindow([string]$ProcessName) {
    foreach ($p in Get-SessionProcess -Name $ProcessName -ErrorAction SilentlyContinue) {
        $h = $p.MainWindowHandle
        if ($h -ne [IntPtr]::Zero -and -not [GolfNative]::IsIconic($h)) { return $h }
    }
    return [IntPtr]::Zero
}

# Launcher steps that read the screen need the app visible, not our window.
function Hide-Launcher {
    if ($window.WindowState -ne 'Minimized') {
        $window.WindowState = 'Minimized'
        Invoke-Pump
    }
}

function Restore-Launcher {
    if ($window.WindowState -eq 'Minimized') {
        $window.WindowState = $(if ($window.WindowStyle -eq 'None') { 'Maximized' } else { 'Normal' })
        [void]$window.Activate()
    }
}

function Find-UiaButton([string]$ProcessName, [string]$Name) {
    $ae = [System.Windows.Automation.AutomationElement]
    $nameCond = New-Object System.Windows.Automation.PropertyCondition($ae::NameProperty, $Name)
    # Prefer a real button: apps can also have a link or label with the same name
    # (Muni Golf's "Play" nav tab vs. its "Play" button).
    $buttonCond = New-Object System.Windows.Automation.AndCondition($nameCond,
        (New-Object System.Windows.Automation.PropertyCondition($ae::ControlTypeProperty, [System.Windows.Automation.ControlType]::Button)))
    $descendants = [System.Windows.Automation.TreeScope]::Descendants
    foreach ($p in Get-SessionProcess -Name $ProcessName -ErrorAction SilentlyContinue) {
        $pidCond = New-Object System.Windows.Automation.PropertyCondition($ae::ProcessIdProperty, $p.Id)
        foreach ($win in $ae::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children, $pidCond)) {
            $button = $win.FindFirst($descendants, $buttonCond)
            # Custom-drawn controls (GSPconnect's "Open Visual Data") aren't buttons.
            if (-not $button) { $button = $win.FindFirst($descendants, $nameCond) }
            if ($button -and $button.Current.IsEnabled) { return $button }
        }
    }
    return $null
}

function Invoke-UiaButton($Button, [string]$ProcessName, [string]$Text) {
    $invoke = [System.Windows.Automation.InvokePattern]::Pattern
    if ($Button.GetSupportedPatterns() -contains $invoke) {
        $Button.GetCurrentPattern($invoke).Invoke()
        Write-Log "    pressed '$Text'"
        return
    }
    # Custom-drawn controls (e.g. GSPconnect's "Open Visual Data") can't be
    # invoked, so click the middle of the control instead.
    $owner = Get-AppWindow $ProcessName
    if ($owner -ne [IntPtr]::Zero) { [void][GolfNative]::SetForegroundWindow($owner); Wait-Seconds 0.3 }
    $box = $Button.Current.BoundingRectangle
    [GolfNative]::Click([int]($box.X + $box.Width / 2), [int]($box.Y + $box.Height / 2))
    Write-Log "    clicked '$Text' at $([int]($box.X + $box.Width / 2)),$([int]($box.Y + $box.Height / 2))"
}

function Stop-ProcessTree([int]$Id) {
    # taskkill writes to stderr when part of a tree is already gone or protected;
    # that's not fatal here, the caller checks what's still running afterwards.
    $old = $ErrorActionPreference
    $ErrorActionPreference = 'SilentlyContinue'
    try { $null = & taskkill.exe /PID $Id /T /F 2>&1 } catch { } finally { $ErrorActionPreference = $old }
    Stop-Process -Id $Id -Force -ErrorAction SilentlyContinue
}

function Test-WindowClass([string]$ProcessName, [string]$ClassName) {
    $ae = [System.Windows.Automation.AutomationElement]
    $classCond = New-Object System.Windows.Automation.PropertyCondition($ae::ClassNameProperty, $ClassName)
    foreach ($p in Get-SessionProcess -Name $ProcessName -ErrorAction SilentlyContinue) {
        $pidCond = New-Object System.Windows.Automation.PropertyCondition($ae::ProcessIdProperty, $p.Id)
        $both = New-Object System.Windows.Automation.AndCondition($pidCond, $classCond)
        if ($ae::RootElement.FindFirst([System.Windows.Automation.TreeScope]::Children, $both)) { return $true }
    }
    return $false
}

$script:ocr = $null
function Initialize-Ocr {
    if ($script:ocr) { return }
    Add-Type -AssemblyName System.Runtime.WindowsRuntime, System.Drawing
    $null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
    $null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
    $null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
    $script:asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    $script:ocr = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
    if (-not $script:ocr) { throw 'Windows text recognition is not available (needs an English language pack)' }
}

function Wait-WinRt($Operation, [Type]$ResultType) {
    $task = $script:asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    while (-not $task.IsCompleted) { Invoke-Pump; Start-Sleep -Milliseconds 20 }
    if ($task.IsFaulted) { throw $task.Exception.InnerException }
    return $task.Result
}

function ConvertTo-OcrWords([string]$Text) {
    return @($Text -split '\s+' | ForEach-Object { ($_ -replace '[^\p{L}\p{N}]', '').ToLowerInvariant() } | Where-Object { $_ })
}

# Screenshots the window, runs OCR and returns the screen point at the centre of
# $Text, preferring a line that is exactly that text (a button) over a sentence
# that merely contains it.
function Find-TextOnWindow([IntPtr]$Handle, [string]$Text) {
    $rect = New-Object GolfNative+RECT
    if (-not [GolfNative]::GetWindowRect($Handle, [ref]$rect)) { return $null }
    $w = $rect.Right - $rect.Left
    $h = $rect.Bottom - $rect.Top
    if ($w -lt 100 -or $h -lt 100) { return $null }

    $tmp = Join-Path $env:TEMP 'golf-launcher-ocr.png'
    $bmp = New-Object System.Drawing.Bitmap($w, $h)
    try {
        $g = [System.Drawing.Graphics]::FromImage($bmp)
        try { $g.CopyFromScreen($rect.Left, $rect.Top, 0, 0, $bmp.Size) } finally { $g.Dispose() }
        $bmp.Save($tmp, [System.Drawing.Imaging.ImageFormat]::Png)
    } finally { $bmp.Dispose() }

    $file = Wait-WinRt ([Windows.Storage.StorageFile]::GetFileFromPathAsync($tmp)) ([Windows.Storage.StorageFile])
    $stream = Wait-WinRt ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder = Wait-WinRt ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap = Wait-WinRt ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        $result = Wait-WinRt ($script:ocr.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    } finally { $stream.Dispose() }

    $want = @(ConvertTo-OcrWords $Text)   # @() keeps a one-word search a list, not a string
    $best = $null
    foreach ($line in $result.Lines) {
        $words = @($line.Words)
        $seen = @($words | ForEach-Object { ($_.Text -replace '[^\p{L}\p{N}]', '').ToLowerInvariant() })
        for ($i = 0; $i -le $seen.Count - $want.Count; $i++) {
            $match = $true
            for ($j = 0; $j -lt $want.Count; $j++) { if ($seen[$i + $j] -ne $want[$j]) { $match = $false; break } }
            if (-not $match) { continue }
            $whole = ($seen.Count -eq $want.Count)
            if (-not $best -or ($whole -and -not $best.Whole)) {
                $best = @{ Words = $words[$i..($i + $want.Count - 1)]; Whole = $whole }
            }
            break
        }
    }
    if (-not $best) { return $null }

    $boxes = $best.Words | ForEach-Object { $_.BoundingRect }
    $left = ($boxes | ForEach-Object { $_.X } | Measure-Object -Minimum).Minimum
    $top = ($boxes | ForEach-Object { $_.Y } | Measure-Object -Minimum).Minimum
    $right = ($boxes | ForEach-Object { $_.X + $_.Width } | Measure-Object -Maximum).Maximum
    $bottom = ($boxes | ForEach-Object { $_.Y + $_.Height } | Measure-Object -Maximum).Maximum
    return @{
        X     = $rect.Left + [int](($left + $right) / 2)
        Y     = $rect.Top + [int](($top + $bottom) / 2)
        Right = $rect.Right
        Bottom = $rect.Bottom
    }
}

# --- steps -------------------------------------------------------------------

function Invoke-Step($Step) {
    $timeout = [double](Get-Opt $Step 'timeout' 60)
    if ($DryRun) { Wait-Seconds 0.4; return 'done' }

    switch ($Step.type) {
        'kill' {
            $procs = @(foreach ($name in @($Step.names)) { Get-SessionProcess -Name $name -ErrorAction SilentlyContinue })
            if ($procs.Count -eq 0) { return 'skipped' }
            if (Get-Opt $Step 'graceful' $false) {
                foreach ($p in $procs) { try { [void]$p.CloseMainWindow() } catch { } }
                $null = Wait-Until { -not ($procs | Where-Object { -not $_.HasExited }) } (Get-Opt $Step 'timeout' 8)
            }
            # Force-kill whole process trees (square_impact runs as a wrapper, a main
            # process and worker processes), then make sure no copy is left behind.
            $names = @($Step.names)
            $script:remaining = @()
            $sweep = {
                $script:remaining = @(foreach ($name in $names) { Get-SessionProcess -Name $name -ErrorAction SilentlyContinue })
                if ($script:remaining.Count -eq 0) { return $true }
                foreach ($p in $script:remaining) { Stop-ProcessTree $p.Id }
                $false
            }
            if (Wait-Until $sweep 10) { return 'done' }

            # Anything left was most likely started "as administrator" (OBS run from an
            # elevated prompt, say), which a normal app can't close. Ask Windows once.
            $list = ($script:remaining | ForEach-Object { $_.Name } | Sort-Object -Unique) -join ', '
            Write-Log "    $list won't close normally; asking for admin rights"
            $pidArgs = ($script:remaining | ForEach-Object { "/PID $($_.Id)" }) -join ' '
            try {
                Start-Process taskkill.exe -ArgumentList "$pidArgs /T /F" -Verb RunAs -WindowStyle Hidden -Wait
            } catch {
                Write-Log "    admin prompt declined: $($_.Exception.Message)"
            }
            if (Wait-Until $sweep 5) { return 'done' }
            throw "$list is running as administrator and couldn't be closed. Close it yourself, then try again."
        }
        'killCommandLine' {
            Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
                Where-Object { $_.SessionId -eq $script:sessionId -and $_.CommandLine -and $_.CommandLine -like $Step.pattern } |
                ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            return 'done'
        }
        'start' {
            $path = Expand-Path $Step.path
            if (-not (Test-Path $path)) { throw "File not found: $path" }
            $exeName = [IO.Path]::GetFileNameWithoutExtension($path)
            if ((Get-Opt $Step 'skipIfRunning' $false) -and (Get-SessionProcess -Name $exeName -ErrorAction SilentlyContinue)) {
                return 'skipped'
            }
            $params = @{
                FilePath         = $path
                PassThru         = $true
                WorkingDirectory = (Expand-Path (Get-Opt $Step 'workingDir' (Split-Path $path)))
            }
            $argList = Get-Opt $Step 'args'
            if ($argList) { $params.ArgumentList = $argList }
            if (Get-Opt $Step 'conhost' $false) {
                # Console apps otherwise open in Windows Terminal, which shows its own
                # taskbar icon; the classic console host shows the app's icon instead.
                $params.FilePath = Join-Path $env:SystemRoot 'System32\conhost.exe'
                $params.ArgumentList = ('"{0}" {1}' -f $path, $argList).Trim()
            }
            if (Get-Opt $Step 'minimized' $false) { $params.WindowStyle = 'Minimized' }
            $proc = Start-Process @params
            if (Get-Opt $Step 'wait' $false) {
                if (-not (Wait-Until { $proc.HasExited } $timeout)) { throw "Still running after $timeout s" }
            }
            return 'done'
        }
        'waitPort' {
            if (-not (Wait-Until { Test-Port $Step.port } $timeout)) {
                throw "Nothing answered on port $($Step.port) after $timeout s"
            }
            return 'done'
        }
        'waitProcess' {
            if (-not (Wait-Until { Get-SessionProcess -Name $Step.name -ErrorAction SilentlyContinue } $timeout)) {
                throw "$($Step.name) didn't start within $timeout s"
            }
            return 'done'
        }
        'deleteFiles' {
            # e.g. OBS's crash markers, so it doesn't ask about Safe Mode after a forced close.
            $files = @(Get-ChildItem -Path (Expand-Path $Step.path) -Force -ErrorAction SilentlyContinue)
            if ($files.Count -eq 0) { return 'skipped' }
            $files | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            Write-Log "    removed $($files.Count) file(s)"
            return 'done'
        }
        'delay' {
            Wait-Seconds (Get-Opt $Step 'seconds' 1)
            return 'done'
        }
        'focus' {
            Wait-Seconds (Get-Opt $Step 'settle' 0)
            Hide-Launcher
            $script:hwnd = [IntPtr]::Zero
            if (-not (Wait-Until { $script:hwnd = Get-AppWindow $Step.process; $script:hwnd -ne [IntPtr]::Zero } $timeout)) {
                throw "No $($Step.process) window to bring forward"
            }
            if (-not [GolfNative]::Focus($script:hwnd)) { throw "Windows wouldn't bring $($Step.process) to the front" }
            return 'done'
        }
        'clickButton' {
            Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes
            $onlyIf = Get-Opt $Step 'ifCommandLine'
            if ($onlyIf) {
                # e.g. only press "Play!" when GSPro was started with its screen selector.
                if (-not (Wait-Until { Get-SessionProcess -Name $Step.process -ErrorAction SilentlyContinue } $timeout)) {
                    throw "$($Step.process) didn't start within $timeout s"
                }
                $cmd = Get-CimInstance Win32_Process -Filter "Name = '$($Step.process).exe' AND SessionId = $($script:sessionId)" -ErrorAction SilentlyContinue |
                    Select-Object -ExpandProperty CommandLine -First 1
                if ($cmd -notlike $onlyIf) { return 'skipped' }
            }
            # doneIfProcess: the button isn't needed if that app is already up
            # (e.g. GSPLauncher starts GSPro by itself when there's no update).
            # doneIfWindowClass: likewise once the process shows that kind of window
            # (e.g. GSPro's game window means there's no screen-selector to confirm).
            $doneIf = Get-Opt $Step 'doneIfProcess'
            $doneIfClass = Get-Opt $Step 'doneIfWindowClass'
            $doneIfPort = Get-Opt $Step 'doneIfPort'
            # clickFirst: press this button first whenever it's enabled (e.g. Square's
            # UPDATE), then keep waiting for the main one (PLAY) until the timeout.
            $first = Get-Opt $Step 'clickFirst'
            $script:firstDone = -not $first
            $deadline = (Get-Date).AddSeconds($timeout)
            while ($true) {
                $script:button = $null
                $script:alreadyUp = $false
                $left = [Math]::Max(1, ($deadline - (Get-Date)).TotalSeconds)
                $found = Wait-Until {
                    if ($doneIf -and (Get-SessionProcess -Name $doneIf -ErrorAction SilentlyContinue)) { $script:alreadyUp = $true; return $true }
                    if ($doneIfClass -and (Test-WindowClass $Step.process $doneIfClass)) { $script:alreadyUp = $true; return $true }
                    if ($doneIfPort -and (Test-Port $doneIfPort)) { $script:alreadyUp = $true; return $true }
                    if (-not $script:firstDone) {
                        $pre = Find-UiaButton $Step.process $first
                        if ($pre) {
                            Invoke-UiaButton $pre $Step.process $first
                            $script:firstDone = $true
                            return $false
                        }
                    }
                    $script:button = Find-UiaButton $Step.process $Step.text
                    [bool]$script:button
                } $left
                if ($script:alreadyUp) { return 'skipped' }
                if (-not $found) { throw "Couldn't find the '$($Step.text)' button after $timeout s" }
                if ($script:firstDone) { break }
                # The main button can be enabled for a moment before the update check
                # finishes, so give the first button a chance to light up.
                Wait-Seconds (Get-Opt $Step 'settle' 2)
                if (-not (Find-UiaButton $Step.process $first)) { $script:firstDone = $true }
            }
            Invoke-UiaButton $script:button $Step.process $Step.text
            return 'done'
        }
        'clickText' {
            Initialize-Ocr
            Hide-Launcher
            $script:hit = $null
            $script:hwnd = [IntPtr]::Zero
            $found = Wait-Until {
                $script:hwnd = Get-AppWindow $Step.process
                if ($script:hwnd -eq [IntPtr]::Zero) { return $false }
                if ([GolfNative]::GetForegroundWindow() -ne $script:hwnd) { [void][GolfNative]::SetForegroundWindow($script:hwnd) }
                $script:hit = Find-TextOnWindow $script:hwnd $Step.text
                return [bool]$script:hit
            } $timeout
            if (-not $found) { throw "Couldn't find '$($Step.text)' on screen after $timeout s" }

            # Menus slide/fade in; look again once they settle so the click lands on the button.
            Wait-Seconds (Get-Opt $Step 'settle' 0.6)
            $again = Find-TextOnWindow $script:hwnd $Step.text
            if ($again) { $script:hit = $again }
            # confirmGone: the screen may show the button before it accepts clicks, so
            # keep clicking until the text goes away (only for text the next screen lacks).
            $tries = $(if (Get-Opt $Step 'confirmGone' $false) { 6 } else { 1 })
            for ($try = 1; $try -le $tries; $try++) {
                [void][GolfNative]::Focus($script:hwnd)
                Wait-Seconds 0.2
                [GolfNative]::Click($script:hit.X, $script:hit.Y)
                Write-Log "    clicked '$($Step.text)' at $($script:hit.X),$($script:hit.Y) (try $try)"
                if ($try -eq $tries) { break }
                Wait-Seconds 2
                $still = Find-TextOnWindow $script:hwnd $Step.text
                if (-not $still) { break }
                $script:hit = $still
            }
            if (Get-Opt $Step 'parkMouse' $false) {
                [void][GolfNative]::SetCursorPos($script:hit.Right - 2, $script:hit.Bottom - 2)
            }
            return 'done'
        }
        default { throw "Unknown step type '$($Step.type)'" }
    }
}

# --- window ------------------------------------------------------------------

$windowXaml = @'
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="Golf Simulator" Width="1400" Height="860" MinWidth="900" MinHeight="600"
        WindowStartupLocation="CenterScreen" Foreground="#F2F5F3" FontFamily="Segoe UI">
  <Window.Background>
    <SolidColorBrush Color="#0B1210"/>
  </Window.Background>

  <Window.Resources>
    <Style x:Key="Card" TargetType="Button">
      <Setter Property="Foreground" Value="#F2F5F3"/>
      <Setter Property="Background" Value="#17221C"/>
      <Setter Property="BorderBrush" Value="#24322A"/>
      <Setter Property="BorderThickness" Value="3"/>
      <Setter Property="Margin" Value="16"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
      <Setter Property="VerticalContentAlignment" Value="Stretch"/>
      <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="{TemplateBinding BorderThickness}" CornerRadius="18">
              <ContentPresenter HorizontalAlignment="{TemplateBinding HorizontalContentAlignment}"
                                VerticalAlignment="{TemplateBinding VerticalContentAlignment}"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#1E2B24"/>
                <Setter TargetName="b" Property="BorderBrush" Value="#F2F5F3"/>
              </Trigger>
              <Trigger Property="IsKeyboardFocused" Value="True">
                <Setter TargetName="b" Property="Background" Value="#1E2B24"/>
                <Setter TargetName="b" Property="BorderBrush" Value="#F2F5F3"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter TargetName="b" Property="Opacity" Value="0.45"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Ghost" TargetType="Button">
      <Setter Property="Foreground" Value="#A9B8B0"/>
      <Setter Property="Background" Value="Transparent"/>
      <Setter Property="BorderBrush" Value="#2C3B33"/>
      <Setter Property="FontSize" Value="20"/>
      <Setter Property="Margin" Value="12,0,0,0"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="2" CornerRadius="24" Padding="26,11">
              <ContentPresenter HorizontalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="BorderBrush" Value="#F2F5F3"/>
              </Trigger>
              <Trigger Property="IsKeyboardFocused" Value="True">
                <Setter TargetName="b" Property="BorderBrush" Value="#F2F5F3"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
    <Style x:Key="Action" TargetType="Button">
      <Setter Property="Foreground" Value="#F2F5F3"/>
      <Setter Property="Background" Value="#17221C"/>
      <Setter Property="BorderBrush" Value="#24322A"/>
      <Setter Property="FontSize" Value="26"/>
      <Setter Property="FontWeight" Value="SemiBold"/>
      <Setter Property="Height" Value="76"/>
      <Setter Property="Margin" Value="16,0,16,12"/>
      <Setter Property="Cursor" Value="Hand"/>
      <Setter Property="HorizontalContentAlignment" Value="Stretch"/>
      <Setter Property="FocusVisualStyle" Value="{x:Null}"/>
      <Setter Property="Template">
        <Setter.Value>
          <ControlTemplate TargetType="Button">
            <Border x:Name="b" Background="{TemplateBinding Background}" BorderBrush="{TemplateBinding BorderBrush}"
                    BorderThickness="3" CornerRadius="14">
              <ContentPresenter HorizontalAlignment="{TemplateBinding HorizontalContentAlignment}" VerticalAlignment="Center"/>
            </Border>
            <ControlTemplate.Triggers>
              <Trigger Property="IsMouseOver" Value="True">
                <Setter TargetName="b" Property="Background" Value="#1E2B24"/>
                <Setter TargetName="b" Property="BorderBrush" Value="#F2F5F3"/>
              </Trigger>
              <Trigger Property="IsKeyboardFocused" Value="True">
                <Setter TargetName="b" Property="Background" Value="#1E2B24"/>
                <Setter TargetName="b" Property="BorderBrush" Value="#F2F5F3"/>
              </Trigger>
              <Trigger Property="IsEnabled" Value="False">
                <Setter TargetName="b" Property="Opacity" Value="0.45"/>
              </Trigger>
            </ControlTemplate.Triggers>
          </ControlTemplate>
        </Setter.Value>
      </Setter>
    </Style>
  </Window.Resources>

  <Grid>
    <Grid.Background>
      <LinearGradientBrush StartPoint="0,0" EndPoint="1,1">
        <GradientStop Color="#0B1210" Offset="0"/>
        <GradientStop Color="#13201A" Offset="1"/>
      </LinearGradientBrush>
    </Grid.Background>
    <Grid x:Name="MenuView" Margin="56,44,56,40">
      <Grid.RowDefinitions>
        <RowDefinition Height="Auto"/>
        <RowDefinition Height="*"/>
        <RowDefinition Height="Auto"/>
      </Grid.RowDefinitions>
      <DockPanel Grid.Row="0" Margin="16,0,16,12">
        <TextBlock x:Name="Clock" DockPanel.Dock="Right" FontSize="30" Foreground="#8FA398" VerticalAlignment="Top"/>
        <StackPanel>
          <TextBlock x:Name="TitleText" FontSize="58" FontWeight="Bold"/>
          <TextBlock x:Name="SubtitleText" FontSize="24" Foreground="#8FA398" Margin="0,2,0,0"/>
        </StackPanel>
      </DockPanel>
      <UniformGrid x:Name="Cards" Grid.Row="1" Rows="1"/>
      <DockPanel Grid.Row="2" Margin="16,14,16,0">
        <StackPanel DockPanel.Dock="Right" Orientation="Horizontal">
          <Button x:Name="CloseAllBtn" Style="{StaticResource Ghost}" Content="Close all sim apps"/>
          <Button x:Name="DesktopBtn" Style="{StaticResource Ghost}" Content="Exit to desktop"/>
        </StackPanel>
        <TextBlock x:Name="HintText" FontSize="20" Foreground="#6B7E73" VerticalAlignment="Center"/>
      </DockPanel>
    </Grid>

    <Grid x:Name="RunView" Visibility="Collapsed">
      <StackPanel VerticalAlignment="Center" HorizontalAlignment="Center" Width="760">
        <Border x:Name="RunAccent" Height="6" Width="120" CornerRadius="3" HorizontalAlignment="Left" Margin="0,0,0,22"/>
        <TextBlock x:Name="RunTitle" FontSize="50" FontWeight="Bold" TextWrapping="Wrap"/>
        <ProgressBar x:Name="RunProgress" Height="8" Margin="0,24,0,22" Background="#24322A" BorderThickness="0"/>
        <StackPanel x:Name="RunSteps"/>
        <StackPanel x:Name="RunButtons" Orientation="Horizontal" Margin="-12,30,0,0" Visibility="Collapsed">
          <Button x:Name="BackBtn" Style="{StaticResource Ghost}" Content="Back to menu"/>
          <Button x:Name="RunDesktopBtn" Style="{StaticResource Ghost}" Content="Exit to desktop"/>
        </StackPanel>
      </StackPanel>
    </Grid>
  </Grid>
</Window>
'@

# Placeholders use @@NAME@@ because {braces} mean markup extensions in XAML.
$cardXaml = @'
<Button xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
  <Grid>
    <Grid.RowDefinitions>
      <RowDefinition Height="10"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>
    <Border Grid.Row="0" Background="@@ACCENT@@" CornerRadius="15,15,0,0"/>
    <Grid Grid.Row="1" Margin="32,26,32,12">
      @@IMAGE@@
      <Border HorizontalAlignment="Left" VerticalAlignment="Top" Background="#0E1512" CornerRadius="10" Padding="16,4">
        <TextBlock Text="@@KEY@@" FontSize="24" FontWeight="SemiBold" Foreground="#8FA398"/>
      </Border>
    </Grid>
    <StackPanel Grid.Row="2" Margin="32,0,32,36">
      <TextBlock Text="@@NAME@@" FontSize="56" FontWeight="Bold" TextWrapping="Wrap"/>
      <TextBlock Text="@@SUBTITLE@@" FontSize="24" Foreground="#A9B8B0" TextWrapping="Wrap" Margin="0,6,0,0"/>
      <TextBlock Text="@@STATUS@@" FontSize="20" Foreground="@@STATUSCOLOR@@" TextWrapping="Wrap" Margin="0,16,0,0"/>
    </StackPanel>
  </Grid>
</Button>
'@

$actionXaml = @'
<Button xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">
  <DockPanel Margin="28,0,22,0">
    <Border DockPanel.Dock="Right" Background="#0E1512" CornerRadius="8" Padding="13,2" VerticalAlignment="Center" Visibility="@@KEYVIS@@">
      <TextBlock Text="@@KEY@@" FontSize="20" FontWeight="SemiBold" Foreground="#8FA398"/>
    </Border>
    <TextBlock Text="@@LABEL@@" VerticalAlignment="Center"/>
  </DockPanel>
</Button>
'@

try {
    $window = [Windows.Markup.XamlReader]::Parse($windowXaml)
} catch {
    Show-Fatal "Launcher UI failed to load.`n`n$($_.Exception.Message)"
    exit 1
}

$ui = @{}
foreach ($name in 'MenuView', 'Clock', 'TitleText', 'SubtitleText', 'Cards', 'CloseAllBtn', 'DesktopBtn', 'HintText',
    'RunView', 'RunAccent', 'RunTitle', 'RunProgress', 'RunSteps', 'RunButtons', 'BackBtn', 'RunDesktopBtn') {
    $ui[$name] = $window.FindName($name)
}

$ui.TitleText.Text = Get-Opt $cfg 'title' 'Golf Simulator'
$ui.SubtitleText.Text = Get-Opt $cfg 'subtitle' 'Choose a simulator to start'
if ($DryRun) { $ui.SubtitleText.Text += '   (dry run - nothing will start)' }
$baseSubtitle = $ui.SubtitleText.Text

$iconPath = Expand-Path (Get-Opt $cfg 'icon')
if ($iconPath -and (Test-Path $iconPath)) {
    try { $window.Icon = [System.Windows.Media.Imaging.BitmapFrame]::Create([Uri]$iconPath) } catch { }
}

if ((Get-Opt $cfg 'fullscreen' $true) -and -not $Windowed) {
    $window.WindowStyle = 'None'
    $window.ResizeMode = 'NoResize'
    $window.WindowState = 'Maximized'
    $window.Topmost = $true
}

$sims = @($cfg.sims)
$cards = New-Object System.Collections.Generic.List[System.Windows.Controls.Button]
$actionKeys = @{}   # key name (e.g. 'R') -> @{ Sim; Action; Button }
$dot = [string][char]0x00B7
$maxActions = ($sims | ForEach-Object { @(Get-Opt $_ 'actions').Where({ $_ }).Count } | Measure-Object -Maximum).Maximum

$hintParts = @("Press 1-$($sims.Count) or tap a simulator")
foreach ($sim in $sims) {
    foreach ($action in @(Get-Opt $sim 'actions')) {
        if ($action -and (Get-Opt $action 'key')) { $hintParts += "$($action.key) for $($sim.name) $($action.label)" }
    }
}
$hintParts += 'Esc for desktop'
$defaultHint = $hintParts -join "   $dot   "
$ui.HintText.Text = $defaultHint

for ($i = 0; $i -lt $sims.Count; $i++) {
    $sim = $sims[$i]
    $state = Get-SimState $sim
    $image = Expand-Path (Get-Opt $sim 'image')
    # No picture configured: a large, faint initial fills the tile instead.
    $imageXaml = '<TextBlock Text="{0}" FontSize="260" FontWeight="Black" Foreground="{1}" Opacity="0.14" HorizontalAlignment="Right" VerticalAlignment="Bottom" Margin="0,0,-8,-40"/>' -f
        (ConvertTo-XmlText ([string]$sim.name).Substring(0, 1)), (ConvertTo-XmlText (Get-Opt $sim 'accent' '#3FA66B'))
    if ($image -and (Test-Path $image)) {
        # Centre the logo in a padded box so wide logos don't touch the edges and
        # tall ones leave room for the title.
        $imageXaml = ('<Grid Margin="0,48,0,0"><Grid.ColumnDefinitions><ColumnDefinition Width="1*"/><ColumnDefinition Width="6*"/><ColumnDefinition Width="1*"/></Grid.ColumnDefinitions>' +
            '<Grid.RowDefinitions><RowDefinition Height="1*"/><RowDefinition Height="4*"/><RowDefinition Height="1*"/></Grid.RowDefinitions>' +
            '<Image Grid.Column="1" Grid.Row="1" Source="{0}" Stretch="Uniform" RenderOptions.BitmapScalingMode="HighQuality"/></Grid>') -f (ConvertTo-XmlText $image)
    }
    $xaml = $cardXaml.
        Replace('@@ACCENT@@', (ConvertTo-XmlText (Get-Opt $sim 'accent' '#3FA66B'))).
        Replace('@@IMAGE@@', $imageXaml).
        Replace('@@KEY@@', [string]($i + 1)).
        Replace('@@NAME@@', (ConvertTo-XmlText $sim.name)).
        Replace('@@SUBTITLE@@', (ConvertTo-XmlText (Get-Opt $sim 'subtitle' ''))).
        Replace('@@STATUS@@', (ConvertTo-XmlText $state.Text)).
        Replace('@@STATUSCOLOR@@', $(if ($state.Ok) { '#6B7E73' } else { '#E8B33A' }))
    $card = [Windows.Markup.XamlReader]::Parse($xaml)
    $card.Style = $window.FindResource('Card')
    $card.IsEnabled = $state.Ok
    $card.Tag = $sim
    $card.Add_Click({ Start-Sim $this.Tag })
    $cards.Add($card)

    # Each column is the tile plus a row of action buttons underneath. Columns
    # with fewer actions get spacers so all tiles stay the same height.
    $column = New-Object System.Windows.Controls.DockPanel
    $actions = @(@(Get-Opt $sim 'actions').Where({ $_ }))
    for ($a = $maxActions - 1; $a -ge 0; $a--) {   # bottom-docked, so add the last one first
        if ($a -lt $actions.Count) {
            $action = $actions[$a]
            $key = [string](Get-Opt $action 'key' '')
            $xaml = $actionXaml.
                Replace('@@LABEL@@', (ConvertTo-XmlText $action.label)).
                Replace('@@KEY@@', (ConvertTo-XmlText $key.ToUpperInvariant())).
                Replace('@@KEYVIS@@', $(if ($key) { 'Visible' } else { 'Collapsed' }))
            $button = [Windows.Markup.XamlReader]::Parse($xaml)
            $button.Style = $window.FindResource('Action')
            $button.BorderBrush = New-Brush (Get-Opt $sim 'accent' '#3FA66B')
            $button.IsEnabled = $state.Ok
            $button.Tag = @{ Sim = $sim; Action = $action }
            $button.Add_Click({ Start-Sim $this.Tag.Sim $this.Tag.Action })
            if ($key) { $actionKeys[$key.ToUpperInvariant()] = @{ Sim = $sim; Action = $action; Button = $button } }
        } else {
            $button = New-Object System.Windows.Controls.Border
            $button.Height = 76
            $button.Margin = [System.Windows.Thickness]::new(16, 0, 16, 12)
        }
        [System.Windows.Controls.DockPanel]::SetDock($button, 'Bottom')
        [void]$column.Children.Add($button)
    }
    [void]$column.Children.Add($card)
    [void]$ui.Cards.Children.Add($column)
}

# --- run view ----------------------------------------------------------------

function Add-StepLine([string]$Text) {
    $row = New-Object System.Windows.Controls.DockPanel
    $row.Margin = [System.Windows.Thickness]::new(0, 5, 0, 5)
    $glyph = New-Object System.Windows.Controls.TextBlock
    $glyph.Width = 44
    $glyph.FontSize = 26
    $label = New-Object System.Windows.Controls.TextBlock
    $label.FontSize = 26
    $label.TextWrapping = 'Wrap'
    $label.Text = $Text
    [System.Windows.Controls.DockPanel]::SetDock($glyph, 'Left')
    [void]$row.Children.Add($glyph)
    [void]$row.Children.Add($label)
    [void]$ui.RunSteps.Children.Add($row)
    return @{ Glyph = $glyph; Label = $label }
}

function Set-StepState($Line, [string]$State, [string]$Detail) {
    $look = @{
        running = @([char]0x25B8, '#F2F5F3')
        done    = @([char]0x2713, '#5BD08A')
        skipped = @([char]0x2013, '#6B7E73')
        warn    = @('!', '#E8B33A')
        failed  = @([char]0x2715, '#F06A6A')
    }[$State]
    $Line.Glyph.Text = [string]$look[0]
    $Line.Glyph.Foreground = New-Brush $look[1]
    $Line.Label.Foreground = New-Brush $(if ($State -eq 'running' -or $State -eq 'done') { '#F2F5F3' } else { $look[1] })
    if ($Detail) { $Line.Label.Text += "`n$Detail" }
}

function Show-Menu {
    $ui.RunView.Visibility = 'Collapsed'
    $ui.MenuView.Visibility = 'Visible'
    $ui.HintText.Text = $defaultHint
    $first = $cards | Where-Object { $_.IsEnabled } | Select-Object -First 1
    if ($first) { [void]$first.Focus() }
}

$script:busy = $false
$script:autoBootAt = $null
$script:autoBootSim = $null

function Show-RunFailure([string]$Title) {
    Restore-Launcher
    $ui.RunTitle.Text = $Title
    $ui.RunButtons.Visibility = 'Visible'
    [void]$ui.BackBtn.Focus()
    $script:busy = $false
}

# Boots a sim; with an action (e.g. Driving Range) the action's steps replace the sim's own.
function Start-Sim($Sim, $Action = $null) {
    if ($script:busy) { return }
    $script:busy = $true
    $script:autoBootAt = $null
    $window.Topmost = $false

    $name = if ($Action) { "$($Sim.name) $([char]0x2013) $($Action.label)" } else { $Sim.name }
    $accent = Get-Opt $Sim 'accent' '#3FA66B'
    $ui.RunAccent.Background = New-Brush $accent
    $ui.RunProgress.Foreground = New-Brush $accent
    $ui.RunTitle.Text = "Starting $name"
    $ui.RunSteps.Children.Clear()
    $ui.RunButtons.Visibility = 'Collapsed'
    $ui.MenuView.Visibility = 'Collapsed'
    $ui.RunView.Visibility = 'Visible'
    Write-Log ("=== Starting {0}{1}" -f $name, $(if ($DryRun) { ' (dry run)' } else { '' }))

    try {
        $own = if ($Action) { Get-Opt $Action 'steps' } else { Get-Opt $Sim 'steps' }
        $steps = @(Resolve-Steps (Get-Opt $cfg 'beforeEachSim')) + @(Resolve-Steps $own)
    } catch {
        $steps = @()
        $line = Add-StepLine 'Reading launcher.json'
        Set-StepState $line 'failed' $_.Exception.Message
        $ui.RunTitle.Text = "Couldn't start $name"
        $ui.RunButtons.Visibility = 'Visible'
        [void]$ui.BackBtn.Focus()
        $script:busy = $false
        return
    }

    $ui.RunProgress.Maximum = [Math]::Max(1, $steps.Count)
    $ui.RunProgress.Value = 0

    foreach ($step in $steps) {
        $text = Get-Opt $step 'label' $step.type
        $line = Add-StepLine $text
        Set-StepState $line 'running'
        Invoke-Pump
        try {
            $result = Invoke-Step $step
            Set-StepState $line $result
            Write-Log "  $result  $text"
        } catch {
            $msg = $_.Exception.Message
            if (Get-Opt $step 'optional' $false) {
                Set-StepState $line 'warn' $msg
                Write-Log "  warn  $text - $msg"
            } else {
                Set-StepState $line 'failed' $msg
                Write-Log "  FAIL  $text - $msg"
                Show-RunFailure "Couldn't start $name"
                return
            }
        }
        $ui.RunProgress.Value += 1
    }

    $ui.RunTitle.Text = "$name is ready"
    Write-Log "=== $name ready"
    Invoke-Pump
    if ($window.WindowState -ne 'Minimized') { Wait-Seconds 1.5 }

    # whenReady: "minimize" (default) parks the menu in the taskbar for switching
    # sims later; "close" exits; "stay" leaves the progress screen up.
    switch (Get-Opt $cfg 'whenReady' 'minimize') {
        'close' { $window.Close() }
        'stay' {
            Restore-Launcher
            $ui.RunButtons.Visibility = 'Visible'
            $script:busy = $false
        }
        default {
            Hide-Launcher
            Show-Menu
            $ui.SubtitleText.Text = "$name is running   $dot   pick a simulator to switch"
            $script:busy = $false
        }
    }
}

# --- events ------------------------------------------------------------------

$ui.DesktopBtn.Add_Click({ $window.Close() })
$ui.RunDesktopBtn.Add_Click({ $window.Close() })
$ui.BackBtn.Add_Click({ Show-Menu })

$ui.CloseAllBtn.Add_Click({
    if ($script:busy) { return }
    $script:busy = $true
    $ui.HintText.Text = 'Closing sim apps...'
    Invoke-Pump
    foreach ($step in @(Resolve-Steps (Get-Opt $cfg 'closeAll'))) {
        try { [void](Invoke-Step $step) } catch { Write-Log "  closeAll: $($_.Exception.Message)" }
    }
    Write-Log '=== Closed sim apps'
    $ui.SubtitleText.Text = $baseSubtitle
    $ui.HintText.Text = 'Sim apps closed.'
    $script:busy = $false
})

function Stop-AutoBoot {
    if ($script:autoBootAt) {
        $script:autoBootAt = $null
        $ui.HintText.Text = $defaultHint
    }
}

$window.Add_PreviewMouseDown({ Stop-AutoBoot })
$window.Add_PreviewKeyDown({
    param($sender, $e)
    Stop-AutoBoot
    if ($script:busy) { return }
    $key = $e.Key.ToString()
    if ($key -match '^(D|NumPad)([1-9])$') {
        $index = [int]$Matches[2] - 1
        if ($ui.MenuView.Visibility -eq 'Visible' -and $index -lt $cards.Count -and $cards[$index].IsEnabled) {
            $e.Handled = $true
            Start-Sim $cards[$index].Tag
        }
    } elseif ($actionKeys.ContainsKey($key) -and $ui.MenuView.Visibility -eq 'Visible') {
        $hit = $actionKeys[$key]
        if ($hit.Button.IsEnabled) {
            $e.Handled = $true
            Start-Sim $hit.Sim $hit.Action
        }
    } elseif ($key -eq 'Escape') {
        $e.Handled = $true
        $window.Close()
    }
})

$timer = New-Object System.Windows.Threading.DispatcherTimer
$timer.Interval = [TimeSpan]::FromMilliseconds(250)
$timer.Add_Tick({
    $ui.Clock.Text = (Get-Date).ToString('h:mm tt')
    if ($script:autoBootAt -and -not $script:busy) {
        $left = [Math]::Ceiling(($script:autoBootAt - (Get-Date)).TotalSeconds)
        if ($left -le 0) {
            $script:autoBootAt = $null
            Start-Sim $script:autoBootSim
        } else {
            $ui.HintText.Text = "Starting $($script:autoBootSim.name) in $left s   $dot   press any key or tap to choose"
        }
    }
})

$window.Add_ContentRendered({
    $ui.Clock.Text = (Get-Date).ToString('h:mm tt')
    $timer.Start()
    Show-Menu

    if ($Select) {
        $simId, $actionId = $Select -split ':', 2
        $sim = $sims | Where-Object { $_.id -eq $simId } | Select-Object -First 1
        $action = if ($actionId) { @(Get-Opt $sim 'actions') | Where-Object { $_ -and $_.id -eq $actionId } | Select-Object -First 1 }
        if ($sim -and (Get-SimState $sim).Ok -and (-not $actionId -or $action)) { Start-Sim $sim $action; return }
        $ui.HintText.Text = "'$Select' isn't available - pick a simulator"
        return
    }

    $seconds = [int](Get-Opt $cfg 'autoBootSeconds' 0)
    $default = $sims | Where-Object { $_.id -eq (Get-Opt $cfg 'defaultSim') } | Select-Object -First 1
    if ($seconds -gt 0 -and $default -and (Get-SimState $default).Ok) {
        $script:autoBootSim = $default
        $script:autoBootAt = (Get-Date).AddSeconds($seconds)
    }
})

# --- go ----------------------------------------------------------------------

try {
    $startup = ((Get-Date) - (Get-Process -Id $PID).StartTime).TotalSeconds
    Write-Log ("Launcher opened in {0:N1} s{1}" -f $startup, $(if ($DryRun) { ' (dry run)' }))
    [void]$window.ShowDialog()
} catch {
    Show-Fatal "The launcher hit an error.`n`n$($_.Exception.Message)"
} finally {
    $timer.Stop()
    $mutex.ReleaseMutex()
}
