param([switch]$Check)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Net.Http

$config = Get-Content (Join-Path $PSScriptRoot 'config.json') -Raw | ConvertFrom-Json
if ($config.Distribution -notmatch '^[a-zA-Z0-9._ -]+$' -or
    $config.WslUser -notmatch '^[a-zA-Z0-9._-]+$' -or
    [int]$config.Port -lt 1 -or [int]$config.Port -gt 65535) { throw 'Configurazione tray non valida.' }
# Use IPv4 explicitly, matching the backend bind and WSL localhost forwarding.
$url = 'http://127.0.0.1:{0}' -f $config.Port
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Launchpad.lnk'
$exitRequest = Join-Path $PSScriptRoot 'exit.request'

function New-LaunchpadIcon([System.Drawing.Color]$color) {
    $bitmap = New-Object System.Drawing.Bitmap 32, 32
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $pen = New-Object System.Drawing.Pen $color, 3
    $brush = New-Object System.Drawing.SolidBrush $color
    $graphics.Clear([System.Drawing.Color]::Transparent)
    $graphics.DrawEllipse($pen, 5, 5, 22, 22)
    $graphics.FillEllipse($brush, 12, 12, 8, 8)
    $graphics.DrawLine($pen, 16, 0, 16, 9)
    $graphics.DrawLine($pen, 16, 23, 16, 32)
    $graphics.DrawLine($pen, 0, 16, 9, 16)
    $graphics.DrawLine($pen, 23, 16, 32, 16)
    $handle = $bitmap.GetHicon()
    try { return [System.Drawing.Icon]::FromHandle($handle).Clone() }
    finally {
        [Launchpad.Native]::DestroyIcon($handle) | Out-Null
        $pen.Dispose(); $brush.Dispose(); $graphics.Dispose(); $bitmap.Dispose()
    }
}

Add-Type @'
using System;
using System.Runtime.InteropServices;
namespace Launchpad {
    public static class Native {
        [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr handle);
    }
}
'@

$onlineIcon = New-LaunchpadIcon ([System.Drawing.Color]::FromArgb(241, 107, 69))
$offlineIcon = New-LaunchpadIcon ([System.Drawing.Color]::Gray)
$notify = New-Object System.Windows.Forms.NotifyIcon
$menu = New-Object System.Windows.Forms.ContextMenuStrip
$statusItem = $menu.Items.Add('Verifica del servizio...')
$statusItem.Enabled = $false
$openItem = $menu.Items.Add('Apri Launchpad')
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
$startItem = $menu.Items.Add('Avvia servizio')
$restartItem = $menu.Items.Add('Riavvia servizio')
$autoItem = $menu.Items.Add('Avvia il tray al login')
$autoItem.Checked = Test-Path $startupLink
$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator)) | Out-Null
$exitItem = $menu.Items.Add('Esci dal tray')
$notify.ContextMenuStrip = $menu
$notify.Icon = $offlineIcon
$notify.Text = 'Launchpad - verifica del servizio'

$handler = New-Object System.Net.Http.HttpClientHandler
$handler.UseProxy = $false
$handler.AllowAutoRedirect = $false
$client = New-Object System.Net.Http.HttpClient $handler
$client.Timeout = [TimeSpan]::FromSeconds(2)
$script:healthTask = $null
$script:serviceProcess = $null
$script:nextCheck = [DateTime]::MinValue

function Show-TrayError([string]$message) {
    $notify.ShowBalloonTip(5000, 'Launchpad', $message, [System.Windows.Forms.ToolTipIcon]::Error)
}

function Start-ServiceAction([string]$action) {
    if ($script:serviceProcess) { return }
    try {
        $info = New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName = Join-Path $env:SystemRoot 'System32\wsl.exe'
        $info.Arguments = '--distribution "{0}" --user "{1}" --exec systemctl --user {2} launchpad.service' -f $config.Distribution, $config.WslUser, $action
        $info.UseShellExecute = $false
        $info.CreateNoWindow = $true
        $info.RedirectStandardError = $true
        $info.RedirectStandardOutput = $true
        $script:serviceProcess = [System.Diagnostics.Process]::Start($info)
        $script:errorTask = $script:serviceProcess.StandardError.ReadToEndAsync()
        $script:outputTask = $script:serviceProcess.StandardOutput.ReadToEndAsync()
        $script:serviceDeadline = [DateTime]::UtcNow.AddSeconds(30)
        $startItem.Enabled = $false
        $restartItem.Enabled = $false
        $statusItem.Text = 'Avvio del comando in WSL...'
    } catch { Show-TrayError $_.Exception.Message }
}

$openItem.Add_Click({
    try { Start-Process $url } catch { Show-TrayError $_.Exception.Message }
})
$notify.Add_DoubleClick({
    try { Start-Process $url } catch { Show-TrayError $_.Exception.Message }
})
$startItem.Add_Click({ Start-ServiceAction 'start' })
$restartItem.Add_Click({ Start-ServiceAction 'restart' })
$autoItem.Add_Click({
    try {
        if (Test-Path $startupLink) { Remove-Item $startupLink -Force }
        else {
            $shell = New-Object -ComObject WScript.Shell
            $shortcut = $shell.CreateShortcut($startupLink)
            $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
            $shortcut.Arguments = '"{0}"' -f (Join-Path $PSScriptRoot 'launchpad.vbs')
            $shortcut.WorkingDirectory = $PSScriptRoot
            $shortcut.Save()
        }
        $autoItem.Checked = Test-Path $startupLink
    } catch { Show-TrayError $_.Exception.Message }
})
$exitItem.Add_Click({ [System.Windows.Forms.Application]::ExitThread() })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 500
$timer.Add_Tick({
    if (Test-Path $exitRequest) {
        Remove-Item $exitRequest -Force
        [System.Windows.Forms.Application]::ExitThread()
        return
    }
    if ($script:serviceProcess) {
        if ($script:serviceProcess.HasExited) {
            if ($script:serviceProcess.ExitCode -ne 0) {
                $detail = $script:errorTask.GetAwaiter().GetResult().Trim()
                Show-TrayError ("Comando WSL fallito. Controlla launchpad.service. $detail")
            }
            $script:serviceProcess.Dispose()
            $script:serviceProcess = $null
            $startItem.Enabled = $true
            $restartItem.Enabled = $true
            $script:nextCheck = [DateTime]::MinValue
        } elseif ([DateTime]::UtcNow -gt $script:serviceDeadline) {
            $script:serviceProcess.Kill()
            Show-TrayError 'WSL non ha risposto entro 30 secondi.'
        }
    }
    if ($script:healthTask -and $script:healthTask.IsCompleted) {
        $online = $false
        try {
            $body = $script:healthTask.GetAwaiter().GetResult() | ConvertFrom-Json
            $online = $body.application -eq 'launchpad' -and $body.status -eq 'ok'
        } catch { $online = $false }
        $script:healthTask = $null
        $notify.Icon = if ($online) { $onlineIcon } else { $offlineIcon }
        $state = if ($online) { 'disponibile' } else { 'offline' }
        $notify.Text = "Launchpad - $state"
        if (-not $script:serviceProcess) { $statusItem.Text = "Dashboard $state" }
        $script:nextCheck = [DateTime]::UtcNow.AddSeconds(8)
    }
    if (-not $script:healthTask -and [DateTime]::UtcNow -ge $script:nextCheck) {
        $script:healthTask = $client.GetStringAsync("$url/api/health")
    }
})

$created = $false
$mutex = New-Object System.Threading.Mutex($true, 'Local\Launchpad.Tray', [ref]$created)
try {
    if ($Check) {
        Write-Output 'Configurazione, icone e menu tray: OK'
        return
    }
    if (-not $created) { return }
    $notify.Visible = $true
    $timer.Start()
    Start-ServiceAction 'start'
    [System.Windows.Forms.Application]::Run()
} finally {
    $timer.Stop(); $timer.Dispose()
    $notify.Visible = $false
    $notify.Dispose(); $menu.Dispose()
    $client.Dispose(); $handler.Dispose()
    $onlineIcon.Dispose(); $offlineIcon.Dispose()
    if ($script:serviceProcess) { $script:serviceProcess.Dispose() }
    if ($created) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
