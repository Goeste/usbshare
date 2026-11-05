# Changelog

Alle nennenswerten Änderungen an usbshare. Format nach
[Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen nach
[Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [0.4.6] – 2026-11-05

### Behoben
- Verschwindet ein Gerät auf dem Server (abgezogen, abgestürzt oder neu angemeldet – z. B.
  ein Funk-Headset-Dongle), trennt der Server die Verbindung jetzt sofort. Bisher blieb sie
  mit einem „toten“ Gerät bestehen, bis der Client sie selbst beendete. Jetzt sieht Windows
  ein sauberes Abstecken, und „Automatisch verbinden“ bindet das Gerät wieder ein, sobald es
  zurück ist.

## [0.4.5] – 2026-11-05

### Behoben
- **Geräte (z. B. Headsets) wurden unter Windows kurz erkannt und verschwanden wieder.**
  usbip-win2 sendet beim Zurücksetzen eines Geräts einen Port-Reset
  (`SET_FEATURE(PORT_RESET)`). Der Server hat diesen bisher als normale Anfrage an das Gerät
  weitergeleitet, das ihn ablehnte – Windows hat das Gerät daraufhin entfernt. Jetzt setzt
  der Server das echte Gerät zurück (wie der Linux-usbip-Server) und meldet Erfolg.
- Fehlgeschlagene Steuer-Anfragen (z. B. `SET_INTERFACE`) stehen jetzt als Warnung im Log.

### Tests
- Isochrone Transfers (USB-Audio: Mikrofon und Lautsprecher) mit echten libusb-Objekten.

### Dokumentation
- Hinweis in README, Paket-Anleitungen und Release-Text: Die Windows-Programme sind nicht
  signiert; Erklärung der SmartScreen-Warnung und wie man die Programme trotzdem startet.

## [0.4.4] – 2026-11-04

### Hinzugefügt
- Beide Download-Pakete enthalten die Lizenz: `LICENSE` (GNU GPL v3), `NOTICE` mit
  Copyright und Zusatzbedingung zur Namensnennung sowie `CHANGELOG.md`.
- Hinweis in README und Paket-Anleitungen: Der Code wurde von Claude (KI-Assistent von
  Anthropic) erstellt und kann Fehler enthalten.
- „Über“-Dialoge und `usbshare --version` nennen Autor, Lizenz und Projektseite.

### Behoben
- Windows-Installationsskript `install-service.ps1` begann mit einer doppelten
  UTF-8-Kennung (BOM), über die Windows PowerShell stolpern konnte.

## [0.4.3] – 2026-11-04

### Behoben
- **Windows meldete „Unbekanntes USB-Gerät (Ungültige Gerätebeschreibung)“, Code 43.**
  Bei Steuer-Anfragen (Endpoint 0) wurden die ersten 8 Byte der Geräte-Antwort
  abgeschnitten, weil `getBuffer()` von python-libusb1 den Setup-Block bereits entfernt.
  Dadurch kam die Gerätebeschreibung verstümmelt an und der Datenstrom zum Client lief aus
  dem Takt. Betraf alle Geräte seit 0.1.0.

### Hinzugefügt
- Schutz im Protokoll: Eine Antwort kündigt nie mehr oder weniger Daten an, als tatsächlich
  folgen.
- Tests mit den echten Transfer-Objekten von python-libusb1.

## [0.4.2] – 2026-11-04

### Behoben
- `number_of_packets` in Antworten wird jetzt so gesetzt, wie der Client es gesendet hat
  (usbip-win2: `0xffffffff` laut Protokoll-Spezifikation, Linux-vhci: `0`).

## [0.4.1] – 2026-11-04

### Hinzugefügt
- USB-Reset beim Verbinden und Trennen (`reset_on_attach`, Standard an), damit der Client
  das Gerät im Grundzustand bekommt (z. B. Logitech-Unifying/Bolt-Empfänger nach
  `hid-logitech-dj`).
- Diagnose im Server-Log: Reset, erste Daten je Endpoint, Fehler je Endpoint,
  Zusammenfassung beim Trennen; Steuer-Anfragen mit `-v`.
- Option `--no-reset`.

### Geändert
- Kurze Datenpakete bei `URB_SHORT_NOT_OK` werden wie im Linux-Kernel mit `-EREMOTEIO`
  samt Daten gemeldet, statt von libusb verworfen zu werden.

## [0.4.0] – 2026-11-04

### Hinzugefügt
- Modernes Design (Windows-11-Stil, hell/dunkel nach Windows-Einstellung), App-Symbole.
- Client und Server-Fenster laufen im Infobereich weiter (Schließen = in den Infobereich),
  mit Status-Symbol, Tooltip und Windows-Benachrichtigungen.
- Erneutes Starten holt das laufende Fenster nach vorn (keine Doppelstarts).
- Server-Fenster: „Beim Anmelden im Infobereich starten“, Konfigurationsdatei öffnen.

### Behoben
- Farbige und graue Beschriftungen wurden vom Theme überschrieben.

## [0.3.0] – 2026-11-04

### Hinzugefügt
- Server-Fenster: Geräte per Klick freigeben/sperren, Geräte bestimmten Clients zuweisen,
  zugelassene Clients verwalten, Geräte vom Client trennen, Liste bekannter Clients.
- Automatische Serversuche im Client (UDP-Broadcast); Server antworten nur zugelassenen
  Clients.
- Konfiguration wird zur Laufzeit übernommen und vom Fenster gespeichert.
- Befehle `usbshare gui`, `usbshare release`; `usbshare remote` zeigt den Status.

## [0.2.0] – 2026-11-04

### Hinzugefügt
- Windows-Client mit Fenster: Serverliste, Geräteansicht mit Status, Verbinden/Trennen,
  „Automatisch verbinden“, Autostart.
- Erweiterte Geräteliste (belegte Geräte, Namen), Freigeben belegter Geräte,
  Zuweisung von Geräten an Clients (`[server.assign]`).

## [0.1.0] – 2026-11-04

### Hinzugefügt
- USB/IP-kompatibler Server für Linux, Windows und macOS auf Basis von libusb.
- Control-, Bulk-, Interrupt- und isochrone Transfers, UNLINK.
- Geräte- und Client-Filter, Autostart-Dateien, Linux-Client-Skript mit Auto-Attach.

[Unreleased]: https://github.com/Goeste/usbshare/compare/v0.4.6...HEAD
[0.4.6]: https://github.com/Goeste/usbshare/releases/tag/v0.4.6
[0.4.5]: https://github.com/Goeste/usbshare/releases/tag/v0.4.5
[0.4.4]: https://github.com/Goeste/usbshare/releases/tag/v0.4.4
[0.4.3]: https://github.com/Goeste/usbshare/releases/tag/v0.4.3
