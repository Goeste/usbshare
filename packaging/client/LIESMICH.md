# usbshare Client für Windows

Bindet USB-Geräte ein, die ein oder mehrere usbshare-Server im Netzwerk anbieten –
mit Fenster, Serverliste und automatischem Verbinden.

## Einrichten (einmalig)

1. **usbip-win2 installieren** – das ist der Windows-Treiber, über den die Geräte
   eingebunden werden: https://github.com/vadimgrn/usbip-win2/releases
   (Installer `USBip-x.x.x-x64.exe` herunterladen und ausführen).
2. `usbshare-client.exe` an einen festen Ort kopieren, z. B. `C:\Program Files\usbshare\`.
3. Starten. Windows fragt nach Administratorrechten – die braucht usbip-win2 zum
   Einbinden von Geräten. SmartScreen warnt beim ersten Start, weil die Datei nicht
   signiert ist: „Weitere Informationen“ → „Trotzdem ausführen“.

## Bedienung

**Server (links)**

* **Server suchen …** – findet usbshare-Server im lokalen Netzwerk automatisch. Es
  antworten nur Server, die diesen PC zulassen. Voraussetzung: die VM ist im selben
  Netzwerk wie der Server (Netzwerkkarte der VM gebrückt, nicht NAT). Beim ersten Start
  sucht der Client von selbst.
* **Server hinzufügen …** – Server von Hand eintragen, falls die Suche ihn nicht findet
  (anderes Subnetz, VPN): Name frei wählbar, Adresse = IP oder Hostname des Rechners mit
  den USB-Geräten (optional mit `:Port`).
* Spalte **Verwenden** anklicken schaltet einen Server an/aus. Nur verwendete Server
  werden laufend abgefragt und automatisch verbunden.
* **Alle Server** zeigt die Geräte aller verwendeten Server zusammen, ein Klick auf einen
  einzelnen Server nur dessen Geräte (auch wenn er gerade nicht verwendet wird).

**Geräte (rechts)** – Status je Gerät:

| Status                    | Bedeutung                                              |
|---------------------------|--------------------------------------------------------|
| Frei                      | kann verbunden werden                                  |
| Verbunden (dieser PC)     | ist an diesem PC eingebunden                           |
| Belegt von 192.168.1.51   | ein anderer PC/eine andere VM nutzt es gerade          |
| Reserviert für …          | am Server einem anderen Client zugewiesen              |
| Server hat keinen Treiber | Windows-Server: UsbDk bzw. WinUSB fehlt dort           |

* **Verbinden / Trennen** – oder Doppelklick auf das Gerät.
* **Automatisch verbinden, sobald verfügbar** – das Gerät wird verbunden, sobald es frei
  ist, auch nach dem Abstecken, einem Server-Neustart oder Netzwerkausfall.
## Infobereich (neben der Uhr)

Der Client muss **nicht** als Fenster offen bleiben:

* **Fenster schließen (X)** → der Client läuft unsichtbar im Infobereich weiter und
  verbindet Geräte automatisch. Ein Doppelklick auf das blaue usbshare-Symbol öffnet das
  Fenster wieder, Rechtsklick → **Beenden** beendet ihn wirklich.
* Das Symbol zeigt den Zustand: **grüner Punkt** = mindestens ein Gerät verbunden,
  **oranger Punkt** = ein Server ist nicht erreichbar. Beim Überfahren mit der Maus steht
  der Status im Tooltip.
* Läuft der Client im Hintergrund, meldet Windows automatisch verbundene Geräte und
  ausgefallene Server per Benachrichtigung.
* Erneutes Starten von `usbshare-client.exe` holt das vorhandene Fenster nach vorn
  (es läuft nie doppelt).
* Tipp: Windows blendet neue Symbole manchmal unter dem Pfeil **^** aus. Zum dauerhaften
  Anzeigen: Einstellungen → Personalisierung → Taskleiste → Andere Taskleistensymbole →
  usbshare einschalten.

Im Menü **⋯** oben rechts:

* **Mit Windows starten (im Infobereich)** – startet den Client bei der Anmeldung direkt
  im Infobereich, ohne UAC-Abfrage.
* **Beim Schließen im Infobereich weiterlaufen** – ausschalten, wenn das X den Client
  komplett beenden soll.
* **Meldungen anzeigen** – Verlauf aller Ereignisse.

Das Design folgt dem hellen bzw. dunklen Modus von Windows.

Die Einstellungen liegen in `%APPDATA%\usbshare\client.json`.

## Hinweise

* Der Client funktioniert auch mit normalen USB/IP-Servern (z. B. Linux `usbipd`); dort
  werden aber keine Gerätenamen und keine belegten Geräte angezeigt.
* Server mit abweichendem Port (nicht 3240) erfordern, dass usbip-win2 die Option
  `--tcp-port` unterstützt – im Zweifel den Standardport verwenden.
* Für Skripte gibt es weiterhin die Kommandozeilenversion im Server-Paket
  (`usbshare.exe remote …` / `usbshare.exe attach …`).
