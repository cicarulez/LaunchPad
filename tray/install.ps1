param(
    [ValidatePattern('^[a-zA-Z0-9._ -]+$')][string]$Distribution = 'Ubuntu',
    [ValidatePattern('^[a-zA-Z0-9._-]+$')][string]$WslUser,
    [ValidateRange(1, 65535)][int]$Port = 7777,
    [switch]$Uninstall,
    [switch]$NoStart
)

$ErrorActionPreference = 'Stop'
$installDir = Join-Path $env:LOCALAPPDATA 'Launchpad'
$startupLink = Join-Path ([Environment]::GetFolderPath('Startup')) 'Launchpad.lnk'
$menuLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'Launchpad.lnk'

if ($Uninstall) {
    # Ask only our tray process to exit; the WSL service stays running.
    if (Test-Path $installDir) { Set-Content (Join-Path $installDir 'exit.request') '' }
    Remove-Item $startupLink, $menuLink -Force -ErrorAction SilentlyContinue
    Write-Output 'Avvio al login e collegamento rimossi. Il servizio WSL resta attivo.'
    Write-Output "I file restano in $installDir e possono essere eliminati dopo la chiusura del tray."
    return
}

if (-not $WslUser) { throw 'Specifica -WslUser con il nome utente Linux che esegue Launchpad.' }
if (Test-Path (Join-Path $installDir 'exit.request')) {
    Remove-Item (Join-Path $installDir 'exit.request') -Force
}
New-Item $installDir -ItemType Directory -Force | Out-Null
Copy-Item (Join-Path $PSScriptRoot 'launchpad-tray.ps1') $installDir -Force
@{ Distribution = $Distribution; WslUser = $WslUser; Port = $Port } |
    ConvertTo-Json | Set-Content (Join-Path $installDir 'config.json') -Encoding UTF8

# wscript keeps both login and manual launches free of console windows.
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$trayScript = Join-Path $installDir 'launchpad-tray.ps1'
$command = '"{0}" -NoProfile -STA -ExecutionPolicy Bypass -WindowStyle Hidden -File "{1}"' -f $powershell, $trayScript
$vbs = 'CreateObject("WScript.Shell").Run "{0}", 0, False' -f $command.Replace('"', '""')
$launcher = Join-Path $installDir 'launchpad.vbs'
Set-Content $launcher $vbs -Encoding Unicode
$shell = New-Object -ComObject WScript.Shell
foreach ($linkPath in @($startupLink, $menuLink)) {
    $shortcut = $shell.CreateShortcut($linkPath)
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $shortcut.Arguments = '"{0}"' -f $launcher
    $shortcut.WorkingDirectory = $installDir
    $shortcut.Description = 'Apri Launchpad dal tray'
    $shortcut.Save()
}
if (-not $NoStart) { Start-Process (Join-Path $env:SystemRoot 'System32\wscript.exe') -ArgumentList ('"{0}"' -f $launcher) }
Write-Output "Tray installato in $installDir per $Distribution / $WslUser, porta $Port."
