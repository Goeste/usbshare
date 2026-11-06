# Translating usbshare · usbshare übersetzen

*Deutsch weiter unten.*

usbshare is written in German; every other language is a translation. Translations are
standard **gettext `.po` files** in [`usbshare/locales/`](usbshare/locales/):

| File | What it is |
|---|---|
| `usbshare.pot` | template with all texts (generated – do not edit) |
| `en.po` | English |
| `<code>.po` | any other language, e.g. `fr.po`, `es.po`, `pl.po` |

A new `.po` file shows up automatically in the language menu (⋯ → **Language**) of the
client and the server window – no code changes needed. Untranslated texts are shown in
German, so a partly finished translation already works.

## Improve an existing translation

1. Open the `.po` file with [Poedit](https://poedit.net/) (free), any other PO editor or a
   text editor.
2. Change the `msgstr` lines. Keep placeholders such as `{device}`, `{n}` or `%s` exactly
   as they are – they are filled in by the program. Keep line breaks (`\n`) where the
   original has them.
3. Open a pull request (or attach the file to an
   [issue](https://github.com/Goeste/usbshare/issues/new?template=translation.md) if you
   do not use git).

## Add a new language

```bash
python tools/i18n.py new fr Français      # language code + name in that language
```

This creates `usbshare/locales/fr.po` from the template. Translate it, then check it:

```bash
python tools/i18n.py check                # placeholders correct? how much is done?
```

Try it without building anything:

```bash
pip install libusb1 sv-ttk
USBSHARE_LANG=fr python -m usbshare --help      # command line
USBSHARE_LANG=fr python server_gui.py           # server window
USBSHARE_LANG=fr python client_gui.py           # client window (Windows)
```

## Rules

- `#, fuzzy` marks an unsure translation; it is **not** used until the flag is removed.
- Lines starting with `#~` are texts the program no longer uses – they can be deleted.
- Use the usual Windows terms of your language (e.g. "notification area" for the tray).
- Write the language name in the header in that language (`X-Language-Name: Français`).

## For developers

Mark every text the user sees with `_()` from `usbshare.i18n`, with **named** placeholders:

```python
from .i18n import _
self.say(_("{device} von {server} verbunden", device=name, server=srv.name))
```

No f-strings inside `_()`. After adding or changing texts run

```bash
python tools/i18n.py extract              # updates usbshare.pot and all .po files
```

and translate the new entries in `en.po`. The CI fails if a text is missing from the
catalogs, if English is incomplete or if placeholders do not match.

---

## Deutsch

usbshare ist auf Deutsch geschrieben; alle anderen Sprachen sind Übersetzungen in
gettext-Dateien (`.po`) im Ordner [`usbshare/locales/`](usbshare/locales/). Eine neue
`.po`-Datei erscheint automatisch im Menü ⋯ → **Sprache**. Nicht übersetzte Texte bleiben
deutsch.

- **Verbessern:** `.po`-Datei mit [Poedit](https://poedit.net/) oder einem Texteditor
  öffnen, `msgstr` ändern, Platzhalter wie `{device}`, `{n}`, `%s` unverändert lassen, Pull
  Request stellen – oder die Datei an ein
  [Issue](https://github.com/Goeste/usbshare/issues/new?template=translation.md) hängen.
- **Neue Sprache:** `python tools/i18n.py new fr Français`, übersetzen, mit
  `python tools/i18n.py check` prüfen, mit `USBSHARE_LANG=fr` ausprobieren.
- **Entwickler:** sichtbare Texte mit `_("… {name} …", name=…)` markieren, danach
  `python tools/i18n.py extract` ausführen und `en.po` ergänzen.
