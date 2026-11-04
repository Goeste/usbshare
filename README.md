# usbshare

**USB-Geräte über das Netzwerk an eine VM oder einen anderen PC durchreichen** – eine freie
Alternative zu VirtualHere. Der Server läuft auf dem Rechner, an dem die Geräte stecken
(Linux oder Windows, experimentell macOS), der Client auf dem Windows-Rechner bzw. der VM,
die die Geräte nutzen soll.

![Client-Fenster](docs/client-fenster.png)

## Funktionen

- **Server** für Linux und Windows, als Dienst mit Autostart
- **Server-Fenster**: Geräte per Klick freigeben, einzelnen Clients zuweisen, zugelassene
  Clients verwalten, Geräte vom Client trennen
- **Windows-Client** mit Fenster: Server automatisch im Netzwerk finden, Geräte mit Status
  sehen (frei / verbunden / belegt / reserviert), verbinden, trennen, „Automatisch verbinden“
- Beide Fenster laufen im **Infobereich** weiter, mit Status-Symbol und Benachrichtigungen;
  hell/dunkel nach Windows-Einstellung
- Spricht das offene **USB/IP-Protokoll**: Als Client dient unter Windows
  [usbip-win2](https://github.com/vadimgrn/usbip-win2), unter Linux der Kernel-Treiber
  `vhci-hcd` – es ist kein eigener Kernel-Treiber nötig
- Control-, Bulk-, Interrupt- und isochrone Transfers; USB-Reset bei der Übergabe

| Server-Fenster | Client (dunkel) |
|---|---|
| ![Server-Fenster](docs/server-fenster.png) | ![Client dunkel](docs/client-fenster-dunkel.png) |

## Download

Fertige Pakete gibt es unter **[Releases](../../releases)**:

| Paket | Für |
|---|---|
| `usbshare-server-<version>.zip` | den Rechner mit den USB-Geräten – Ordner `linux/` und `windows/` |
| `usbshare-client-<version>-windows.zip` | die Windows-VM bzw. den Windows-PC, der die Geräte nutzt |

Die Anleitungen liegen als `LIESMICH.md` in den Paketen
([Server](packaging/server/LIESMICH.md), [Client](packaging/client/LIESMICH.md)).

## Kurzanleitung

**Server unter Linux**

```bash
cd usbshare-server/linux
sudo ./install.sh
```

**Server unter Windows** – zuerst [UsbDk](https://github.com/daynix/UsbDk/releases)
installieren, dann PowerShell als Administrator:

```powershell
cd usbshare-server\windows
Set-ExecutionPolicy -Scope Process Bypass
.\install-service.ps1 -UsbDk
```

**Client (Windows-VM)** – [usbip-win2](https://github.com/vadimgrn/usbip-win2/releases)
installieren, `usbshare-client.exe` starten, „Server suchen“. Die VM muss im selben
Netzwerk wie der Server sein (Netzwerkkarte gebrückt, nicht NAT).

## Aus dem Quellcode starten und bauen

```bash
pip install libusb1 sv-ttk pillow          # Windows zusätzlich: pystray
python -m usbshare list                    # lokale USB-Geräte
sudo python -m usbshare serve --config usbshare.toml
python -m usbshare gui                     # Server-Fenster
python client_gui.py                       # Client-Fenster
python -m unittest discover -s tests -t .  # Tests
```

Einzelne Programme mit PyInstaller (jeweils auf dem Zielsystem):

```bash
pip install pyinstaller libusb1 sv-ttk pillow pystray
python build.py              # usbshare(.exe): Server + Kommandozeile (+ Server-Fenster unter Linux)
python build.py gui          # usbshare-client.exe
python build.py server-gui   # usbshare-server-gui.exe
python packaging/make_packages.py --version 0.4.3 --bins dist --out out
```

**Releases per GitHub Actions:** Version in `usbshare/__init__.py` erhöhen, den Abschnitt in
[CHANGELOG.md](CHANGELOG.md) ergänzen und auf `main` pushen – der Workflow „Build & Release“
baut dann Linux, Windows und experimentell macOS, schnürt die Pakete und veröffentlicht das
Release `v<version>` mit dem Changelog-Abschnitt. Alternativ einen Tag `v<version>` pushen.
Über „Run workflow“ lässt sich der Build auch ohne Release starten.

## Aufbau

| Pfad | Inhalt |
|---|---|
| `usbshare/protocol.py` | USB/IP-Protokoll |
| `usbshare/server.py`, `libusb_backend.py` | Server, USB-Zugriff über libusb |
| `usbshare/config.py` | Server-Konfiguration (`usbshare.toml`) |
| `usbshare/gui.py`, `gui_model.py` | Windows-Client-Fenster |
| `usbshare/server_gui.py` | Server-Fenster |
| `usbshare/ui_common.py`, `icons.py` | Design, Infobereich, Symbole |
| `packaging/` | Installationsskripte und Anleitungen der Pakete |
| `contrib/` | Linux-Client-Skript (Auto-Attach), macOS-launchd |
| `tests/` | Tests (Protokoll, Server, Client-Logik, libusb-Puffer) |

## Sicherheit

USB/IP ist unverschlüsselt und ohne Passwort. Port 3240 (TCP/UDP) nie ins Internet
freigeben und die zugelassenen Clients auf die eigenen VMs beschränken; für Zugriffe von
außen ein VPN verwenden.

## Lizenz

Copyright (C) 2026 Goeste

usbshare ist freie Software unter der **GNU General Public License v3.0 oder neuer**
([LICENSE](LICENSE)) mit einer **zusätzlichen Bedingung zur Namensnennung** nach
Abschnitt 7 b) ([NOTICE](NOTICE)):

- Du darfst den Code nutzen, verändern und weitergeben.
- Wer eine (veränderte) Version weitergibt, muss sie ebenfalls unter der GPL v3 mit
  vollständigem Quellcode weitergeben – sie bleibt damit frei und Open Source.
- Der ursprüngliche Autor muss genannt werden: „Basiert auf usbshare von Goeste
  (https://github.com/Goeste/usbshare)“ in der README bzw. Dokumentation und, falls vorhanden, im
  „Über“-Dialog.

Die genutzten Bibliotheken stehen unter eigenen, GPL-kompatiblen Lizenzen
(libusb/python-libusb1: LGPL-2.1, sv-ttk: MIT, Pillow: MIT-CMU, pystray: LGPL-3.0).
