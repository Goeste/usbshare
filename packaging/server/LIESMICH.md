# usbshare – Server

> **Hinweis – mit KI erstellt:** Der Code wurde von Claude (KI-Assistent von Anthropic) für
> Goeste geschrieben. Er wurde getestet, kann aber Fehler enthalten. Nutzung auf eigenes
> Risiko. Fehler bitte melden: https://github.com/Goeste/usbshare/issues

Läuft auf dem Rechner, an dem die USB-Geräte **eingesteckt** sind, und bietet sie im
Netzwerk an. Die VM bindet sie mit dem **usbshare Client** ein (Paket `usbshare-client`).

Pakete: `usbshare-server-<version>-linux-x64.zip` (Linux, Ordner `linux`) und
`usbshare-server-<version>-win-x64.zip` (Windows, Ordner `win`).

Am Server legst du fest:

* **welche Geräte** freigegeben werden,
* **welche Clients** sich überhaupt verbinden dürfen,
* **welcher Client welches Gerät** nutzen darf.

Das geht bequem im Fenster **usbshare Server** (siehe unten) oder in der Datei
`usbshare.toml`.

## Installation

### Windows

1. `usbshare-server-<version>-win-x64.zip` entpacken.
2. **Treiber**: Windows lässt Programme nicht ohne Weiteres auf USB-Geräte zugreifen.
   * Empfohlen: **UsbDk** installieren (github.com/daynix/UsbDk/releases, `UsbDk_*_x64.msi`).
     Funktioniert mit allen Geräten, ohne Treiber zu tauschen.
   * Alternativ: mit **Zadig** dem einzelnen Gerät den Treiber „WinUSB“ zuweisen.
3. PowerShell **als Administrator** im Ordner `usbshare-server\win` öffnen:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\install-service.ps1 -UsbDk          # mit UsbDk (ohne -UsbDk bei Zadig/WinUSB)
```

Das installiert nach `C:\Program Files\usbshare`, richtet den Autostart ein, gibt Port
3240 (TCP und UDP) für private Netzwerke in der Firewall frei, legt im Startmenü
**usbshare Server** an und öffnet das Fenster.

* Entfernen: `.\install-service.ps1 -Uninstall`
* Zum Ausprobieren ohne Installation: `Server-starten.bat` als Administrator, dann
  `usbshare-server-gui.exe` öffnen.
* Windows warnt beim ersten Start, weil die Dateien **nicht signiert** sind – siehe
  „Windows-Warnung“ weiter unten.

### Linux

```bash
cd linux
sudo ./install.sh
```

Installiert nach `/usr/local/bin`, richtet einen systemd-Dienst ein, gibt Port 3240
(TCP und UDP) in ufw/firewalld frei und legt einen Menü-Eintrag **usbshare Server** an.
Das Fenster öffnet sich auch mit `usbshare gui` (als normaler Benutzer, ohne sudo).

* Log: `journalctl -u usbshare -f`
* Entfernen: `sudo ./install.sh --uninstall`
* Benötigt ein aktuelles System (ca. Ubuntu 24.04 / Debian 13 oder neuer).

### Windows-Warnung: Die Programme sind nicht signiert

Die `.exe`-Dateien sind **nicht digital signiert** (es gibt noch kein
Code-Signing-Zertifikat). Windows zeigt deshalb beim ersten Start eine Warnung von **Microsoft Defender
SmartScreen** („Der Computer wurde durch Windows geschützt“) und bei der Rechteabfrage
„Unbekannter Herausgeber“ an. Das ist bei nicht signierten Programmen normal und kein
Hinweis auf Schadsoftware.

**So startest du die Programme trotzdem:**

- **Im Warnfenster:** auf **„Weitere Informationen“** klicken, dann **„Trotzdem
  ausführen“**. Windows merkt sich das für diese Datei.
- **Oder vor dem Entpacken freigeben** (gilt dann für alle Dateien): Rechtsklick auf die
  heruntergeladene ZIP → **Eigenschaften** → unten **„Zulassen“** anhaken → OK, erst dann
  entpacken. Alternativ im entpackten Ordner in PowerShell:
  `Get-ChildItem -Recurse | Unblock-File`
- **Kein „Trotzdem ausführen“?** Dann ist wahrscheinlich **Smart App Control** aktiv
  (häufig bei neu installiertem Windows 11), das nicht signierte Programme komplett
  blockiert. Die Freigabe über „Zulassen“ hilft meist; sonst Windows-Sicherheit →
  App- & Browsersteuerung → Smart App Control. Achtung: Je nach Windows-Version lässt es
  sich danach nicht ohne Weiteres wieder einschalten.

**Woher weiß ich, dass die Datei sauber ist?** Die Release-Dateien werden ausschließlich
von GitHub Actions aus genau diesem Quellcode gebaut – nachvollziehbar unter
[Actions](https://github.com/Goeste/usbshare/actions). Wer ganz sicher gehen will, baut
die Programme selbst (Anleitung in der README unter https://github.com/Goeste/usbshare). Meldet
ein Virenscanner die Datei (PyInstaller-Programme werden gelegentlich fälschlich
erkannt), bitte ein [Issue](https://github.com/Goeste/usbshare/issues) anlegen.

Eine Signierung (über die SignPath Foundation für Open-Source-Projekte) ist geplant.

## Das Server-Fenster

**USB-Geräte an diesem Rechner**

| Spalte       | Bedeutung                                                         |
|--------------|-------------------------------------------------------------------|
| Freigegeben  | ✔ = Clients können das Gerät nutzen. Anklicken schaltet um.       |
| Zugriff für  | „Alle Clients“ oder nur bestimmte Clients                         |
| Status       | Frei / Verbunden mit … / Nicht freigegeben / Kein Treiber         |

* **Clients festlegen …** (oder Doppelklick): bestimmen, welche Clients dieses Gerät
  nutzen dürfen. Alle anderen sehen es im Client als „Reserviert“.
* **Vom Client trennen**: Gerät einem Client wegnehmen, z. B. wenn eine VM hängt. Für den
  Client sieht das wie Abstecken aus.
* Wird ein Gerät gesperrt oder einem anderen Client zugewiesen, während es benutzt wird,
  wird es dort automatisch getrennt.

**Clients**

Zeigt alle Rechner/VMs, die sich gemeldet haben – mit Namen, ob sie zugelassen sind und
welche Geräte sie gerade nutzen. Über **Zugelassene Clients …** legst du fest, wer sich
überhaupt verbinden darf („Alle Clients im Netzwerk“ oder nur ausgewählte).

Alle Änderungen gelten sofort und werden in `usbshare.toml` gespeichert. Das Fenster
selbst braucht keine Administratorrechte; es spricht mit dem laufenden Dienst, und das
geht nur auf dem Server-Rechner selbst.

**Muss das Fenster offen bleiben? Nein.** Der Server ist ein Dienst und läuft immer im
Hintergrund – auch ohne angemeldeten Benutzer. Das Fenster ist nur zum Einstellen und
Nachsehen da:

* **Schließen (X)** legt das Fenster in den Infobereich neben der Uhr (grünes Symbol).
  Doppelklick öffnet es wieder; Rechtsklick → „Fenster beenden“ schließt es ganz – der
  Dienst läuft trotzdem weiter.
* Im Infobereich zeigt ein **grüner Punkt**, dass gerade ein Client ein Gerät nutzt, ein
  **oranger Punkt**, dass der Dienst nicht läuft. Verbindet oder trennt ein Client ein
  Gerät, meldet Windows das per Benachrichtigung.
* Menü **⋯** → **Beim Anmelden im Infobereich starten**, wenn das Symbol immer da sein
  soll; **Beim Schließen im Infobereich bleiben** ausschalten, wenn X das Fenster ganz
  schließen soll.

## Wie findet der Client den Server?

Im usbshare Client auf **Server suchen …** klicken. Der Client fragt per Broadcast im
lokalen Netzwerk nach; es antworten nur Server, die **diesen Client zulassen**. Damit das
klappt:

* Client (VM) und Server müssen im **selben Netzwerk** sein. Bei einer VM heißt das: die
  Netzwerkkarte der VM ist **gebrückt** (Bridge), nicht hinter NAT.
* UDP-Port 3240 darf nicht von einer Firewall blockiert sein (macht die Installation).

Klappt die Suche nicht (z. B. anderes Subnetz, VPN), trägt man den Server im Client mit
**Server hinzufügen …** und seiner IP-Adresse ein. Die Serversuche lässt sich in
`usbshare.toml` mit `discovery = false` abschalten.

## Konfigurationsdatei `usbshare.toml`

Wird vom Fenster geschrieben, kann aber auch von Hand bearbeitet werden (Änderungen werden
nach wenigen Sekunden übernommen; `host`, `port` und `usbdk` erst nach Neustart).

| Eintrag           | Bedeutung                                                     |
|-------------------|---------------------------------------------------------------|
| `allow_clients`   | nur diese IPs/Netze dürfen sich verbinden (leer = alle)       |
| `only`            | nur diese Geräte freigeben (`vid:pid`, `vid:*`, Bus-ID)       |
| `exclude`         | diese Geräte nie freigeben                                    |
| `discovery`       | auf die Serversuche der Clients antworten                     |
| `usbdk`           | Windows: UsbDk verwenden                                      |
| `[server.assign]` | `"046d:c52b" = ["192.168.1.50"]` – Gerät nur für diese Clients |

## Updates

* **Linux:** `sudo usbshare update` – lädt die neueste Version von GitHub, prüft die
  Prüfsumme, testet das neue Programm, ersetzt es und startet den Dienst neu (verbundene
  Geräte werden dabei kurz getrennt). `usbshare update --check` zeigt nur, ob es ein Update
  gibt. Im Server-Fenster: Knopf **„Nach Updates suchen“** → „Jetzt aktualisieren“ (Linux
  fragt nach deinem Passwort). Die Konfiguration bleibt erhalten.
* **Windows:** im Server-Fenster **„Nach Updates suchen“** → „Jetzt aktualisieren“
  (Windows fragt nach Administratorrechten) oder in einer Eingabeaufforderung **als
  Administrator**: `"C:\Program Files\usbshare\usbshare.exe" update`. Lädt das
  Windows-Paket, prüft die Prüfsumme, testet das neue Programm, ersetzt `usbshare.exe` und
  `usbshare-server-gui.exe` und startet den Dienst neu. Die Konfiguration bleibt erhalten.
  (Ein neues Paket per `install-service.ps1` zu installieren geht weiterhin.)

## Kommandozeile

```
usbshare list                  # lokale USB-Geräte
usbshare remote 127.0.0.1      # Geräte mit Status (frei / belegt von … / reserviert)
usbshare release 1-4           # Gerät vom Client lösen
sudo usbshare update           # auf die neueste Version aktualisieren (Windows: als Administrator)
usbshare gui                   # Server-Fenster (Linux; Windows: usbshare-server-gui.exe)
```

**Sicherheit:** Die Verbindung ist unverschlüsselt und ohne Passwort. Port 3240 nie ins
Internet freigeben und die zugelassenen Clients auf deine VMs beschränken.

## Fehlersuche

**Windows meldet „Unbekanntes USB-Gerät (Fehler beim Anfordern einer Gerätebeschreibung)“
bzw. „ungültige Gerätebeschreibung“ (Code 43):** Behoben in Version 0.4.3. Bis 0.4.2 wurden
bei Steuer-Anfragen die ersten 8 Byte der Antwort des Geräts abgeschnitten – Windows bekam
eine verstümmelte Gerätebeschreibung. Außerdem setzt der Server seit 0.4.2 ein Protokollfeld
(`number_of_packets`) so, wie usbip-win2 es erwartet. Server auf 0.4.3 aktualisieren, Dienst neu starten, Gerät im Client trennen und neu verbinden. Falls
Windows das Gerät danach noch als „Unbekannt“ im Geräte-Manager führt: dort deinstallieren
und erneut verbinden.

**Gerät ist beim Client „verbunden“, reagiert aber nicht** (z. B. Tastatur/Maus über einen
Logitech-Unifying- oder Bolt-Empfänger): Seit Version 0.4.1 setzt der Server jedes Gerät
beim Verbinden per USB-Reset zurück, damit der Client es im Grundzustand bekommt. Unter
Linux schaltet der Treiber `hid-logitech-dj` solche Empfänger in einen Sondermodus, in dem
sie Tastatur- und Maus-Eingaben nicht mehr auf dem normalen Weg senden – ohne Reset kommt
beim Client dann nichts an. Nach dem Update: Gerät im Client trennen und neu verbinden.

**Was im Server-Log steht** (Linux: `journalctl -u usbshare -n 80 --no-pager` – ohne
`--no-pager` werden lange Zeilen abgeschnitten –, live: `journalctl -u usbshare -f`, Windows:
`C:\Program Files\usbshare\server.log`):

| Meldung                                              | Bedeutung                                      |
|------------------------------------------------------|------------------------------------------------|
| `1-4: device reset`                                  | Gerät wurde beim Verbinden zurückgesetzt       |
| `first data from endpoint 0x81 … device is sending`  | Das Gerät liefert Daten an den Client – gut     |
| `endpoint 0x82: error -32 (stall)`                   | Das Gerät lehnt eine Anfrage ab                |
| `cannot claim interface 2`                           | Ein Teil des Geräts ist noch von einem anderen Programm/Treiber belegt |
| `session ended – … Daten von Endpoints: …, Fehler: …`| Zusammenfassung beim Trennen                   |

Für noch mehr Details den Server mit `-v` starten (Linux: in `/etc/systemd/system/usbshare.service`
`usbshare -v serve …`, dann `sudo systemctl daemon-reload && sudo systemctl restart usbshare`).

**Headset: Ton stockt oder ist stumm** (z. B. Funk-Dongles): Der Server puffert die
Audio-Wiedergabe (`audio_buffer_ms = 40` in `usbshare.toml`) und füllt verspätete Daten mit
Stille auf, damit der Dongle nicht stumm schaltet. Im Log beim Trennen steht
`Audio-Puffer: … mit Stille aufgefüllt N×`. Ist N hoch, kommen die Daten über das Netzwerk
zu spät – dann `audio_buffer_ms` erhöhen (z. B. 80; mehr Verzögerung) oder den Server per
Kabel anbinden. Mit `0` ist der Puffer aus.

**Gerät verträgt den Reset nicht** (sehr selten): in `usbshare.toml`
`reset_on_attach = false` setzen und den Dienst neu starten.
