# usbshare-Server unter Windows installieren (PowerShell als Administrator):
#   .\install-service.ps1                                  alle Clients erlaubt
#   .\install-service.ps1 -UsbDk                           mit UsbDk-Treiber (empfohlen)
#   .\install-service.ps1 -AllowClient 192.168.1.50        nur diese VM
#   .\install-service.ps1 -Uninstall
# Geräte freigeben und Clients zuweisen geht danach bequem im Fenster
# "usbshare Server" (Startmenü).
param(
    [string[]]$AllowClient = @(),
    [switch]$UsbDk,
    [switch]$Uninstall
)
$ErrorActionPreference = "Stop"
$name     = "usbshare"
$target   = "C:\Program Files\usbshare"
$shortcut = "$env:ProgramData\Microsoft\Windows\Start Menu\Programs\usbshare Server.lnk"

function Remove-Firewall {
    foreach ($n in "usbshare (TCP 3240)", "usbshare (UDP 3240, Serversuche)") {
        Remove-NetFirewallRule -DisplayName $n -ErrorAction SilentlyContinue
    }
}

if ($Uninstall) {
    Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Get-Process usbshare, usbshare-server-gui -ErrorAction SilentlyContinue | Stop-Process -Force
    Remove-Firewall
    Remove-Item $shortcut -ErrorAction SilentlyContinue
    "usbshare entfernt. Der Ordner $target (mit der Konfiguration) kann gelöscht werden."
    return
}

New-Item -ItemType Directory -Force -Path $target | Out-Null
Get-Process usbshare, usbshare-server-gui -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Milliseconds 500
Copy-Item "$PSScriptRoot\usbshare.exe" "$target\usbshare.exe" -Force
Copy-Item "$PSScriptRoot\usbshare-server-gui.exe" "$target\usbshare-server-gui.exe" -Force
$config = "$target\usbshare.toml"
if (-not (Test-Path $config)) { Copy-Item "$PSScriptRoot\usbshare.toml" $config }

$toml = Get-Content $config -Raw
if ($AllowClient.Count -gt 0) {
    $list = ($AllowClient | ForEach-Object { "`"$_`"" }) -join ", "
    $toml = $toml -replace '(?m)^allow_clients = .*$', "allow_clients = [$list]"
}
if ($UsbDk) { $toml = $toml -replace '(?m)^usbdk = .*$', 'usbdk = true' }
[IO.File]::WriteAllText($config, $toml)

# Dienst (geplante Aufgabe als SYSTEM beim Systemstart)
$cmd = "`"$target\usbshare.exe`" serve --config `"$config`" >> `"$target\server.log`" 2>&1"
$action    = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$cmd`""
$trigger   = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
             -ExecutionTimeLimit ([TimeSpan]::Zero) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Principal $principal `
    -Settings $settings -Force | Out-Null

# Firewall: TCP für die Geräte, UDP für die automatische Serversuche der Clients
Remove-Firewall
New-NetFirewallRule -DisplayName "usbshare (TCP 3240)" -Direction Inbound -Protocol TCP `
    -LocalPort 3240 -Action Allow -Profile Private,Domain | Out-Null
New-NetFirewallRule -DisplayName "usbshare (UDP 3240, Serversuche)" -Direction Inbound -Protocol UDP `
    -LocalPort 3240 -Action Allow -Profile Private,Domain | Out-Null

# Startmenü-Eintrag für das Server-Fenster
$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($shortcut)
$lnk.TargetPath = "$target\usbshare-server-gui.exe"
$lnk.WorkingDirectory = $target
$lnk.Description = "USB-Geräte freigeben und Clients zuweisen"
$lnk.Save()

Start-ScheduledTask -TaskName $name
Start-Sleep -Seconds 2
""
"Fertig - der Server läuft und startet automatisch mit Windows."
"Geräte freigeben und zuweisen: Startmenü -> 'usbshare Server'"
""
"  Konfiguration: $config"
"  Log:           $target\server.log"
"  IP-Adressen:   " + ((Get-NetIPAddress -AddressFamily IPv4 | Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' }).IPAddress -join ", ")
""
"Hinweis: Firewall-Regeln gelten für private Netzwerke. Ist dein Netzwerk in Windows als"
"'Öffentlich' eingestuft, finden die Clients den Server nicht."
Start-Process "$target\usbshare-server-gui.exe"
