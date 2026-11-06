# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Translations.

The texts in the source code are German. Translations live in gettext files
``usbshare/locales/<code>.po`` (edit them with Poedit, Weblate, a text editor …). A new
.po file is picked up automatically and offered in the language menu – see TRANSLATING.md.

    from .i18n import _
    _("Version {v} installiert", v=version)

Placeholders are named ({v}); translations must keep them. The language is chosen once at
program start: environment variable USBSHARE_LANG, else the saved choice (menu "Sprache"),
else the system language; unknown languages fall back to English, German stays German.
"""
from __future__ import annotations

import glob
import os
import sys
from typing import Dict, List, Optional, Tuple

SOURCE = "de"                     # language of the texts in the code
FALLBACK = "en"                   # for system languages without a translation
LOCALES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
NAMES = {"de": "Deutsch"}

_catalog: Dict[str, str] = {}
_lang = SOURCE


# -- .po files -----------------------------------------------------------------------------
def _unquote(s: str) -> str:
    s = s.strip()
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        raise ValueError(s)
    out, i, body = [], 0, s[1:-1]
    esc = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "r": "\r"}
    while i < len(body):
        c = body[i]
        if c == "\\" and i + 1 < len(body):
            out.append(esc.get(body[i + 1], body[i + 1]))
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


def parse_po(text: str) -> List[Tuple[str, str, List[str]]]:
    """-> [(msgid, msgstr, flags)], header entry (msgid "") included. Obsolete (#~) entries
    are skipped."""
    entries: List[Tuple[str, str, List[str]]] = []
    st = {"id": None, "str": None, "flags": [], "field": None}

    def flush():
        if st["id"] is not None:
            entries.append((st["id"], st["str"] or "", st["flags"]))
        st.update(id=None, str=None, flags=[], field=None)

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            flush()
        elif line.startswith("#~"):
            continue
        elif line.startswith("#"):
            if st["str"] is not None:
                flush()
            if line.startswith("#,"):
                st["flags"] = [f.strip() for f in line[2:].split(",") if f.strip()]
        elif line.startswith("msgid "):
            if st["str"] is not None:
                flush()
            st["id"], st["field"] = _unquote(line[6:]), "id"
        elif line.startswith("msgstr "):
            st["str"], st["field"] = _unquote(line[7:]), "str"
        elif line.startswith('"') and st["field"]:
            st[st["field"]] += _unquote(line)
    flush()
    return entries


def po_header(entries) -> Dict[str, str]:
    for msgid, msgstr, _f in entries:
        if msgid == "":
            out = {}
            for ln in msgstr.splitlines():
                if ":" in ln:
                    k, v = ln.split(":", 1)
                    out[k.strip()] = v.strip()
            return out
    return {}


def load_catalog(path: str) -> Dict[str, str]:
    with open(path, encoding="utf-8") as f:
        entries = parse_po(f.read())
    return {i: s for i, s, flags in entries if i and s and "fuzzy" not in flags}


# -- languages -------------------------------------------------------------------------------
def available() -> Dict[str, str]:
    """code -> name of the language (in that language), German first."""
    langs = dict(NAMES)
    for p in sorted(glob.glob(os.path.join(LOCALES, "*.po"))):
        code = os.path.splitext(os.path.basename(p))[0]
        try:
            with open(p, encoding="utf-8") as f:
                name = po_header(parse_po(f.read())).get("X-Language-Name") or code
        except (OSError, ValueError):
            continue
        langs[code] = name
    return langs


def config_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "usbshare")


def _pref_file() -> str:
    return os.path.join(config_dir(), "language")


def saved_choice() -> str:
    """'auto' or a language code."""
    try:
        with open(_pref_file(), encoding="utf-8") as f:
            return f.read().strip() or "auto"
    except OSError:
        return "auto"


def save_choice(choice: str) -> None:
    os.makedirs(config_dir(), exist_ok=True)
    with open(_pref_file(), "w", encoding="utf-8") as f:
        f.write(choice + "\n")


def system_language() -> str:
    if sys.platform.startswith("win"):
        try:
            import ctypes
            import locale
            name = locale.windows_locale.get(ctypes.windll.kernel32.GetUserDefaultUILanguage(), "")
            if name:
                return name.split("_")[0].lower()
        except Exception:
            pass
    for var in ("LC_ALL", "LC_MESSAGES", "LANG", "LANGUAGE"):
        v = os.environ.get(var, "")
        if v and v not in ("C", "POSIX") and not v.startswith("C."):
            return v.split(":")[0].split("_")[0].split(".")[0].lower()
    return ""


def resolve(choice: Optional[str] = None) -> str:
    """Language to use for a choice ('auto' or a code)."""
    langs = available()
    choice = (choice or "auto").lower()
    if choice != "auto":
        if choice in langs:
            return choice
        choice = choice.split("_")[0].split("-")[0]
        if choice in langs:
            return choice
    sysl = system_language()
    if sysl in langs:
        return sysl
    return FALLBACK if FALLBACK in langs else SOURCE


def set_language(code: str) -> str:
    global _catalog, _lang
    code = resolve(code)
    cat: Dict[str, str] = {}
    if code != SOURCE:
        try:
            cat = load_catalog(os.path.join(LOCALES, code + ".po"))
        except (OSError, ValueError):
            code = SOURCE
    _catalog, _lang = cat, code
    return code


def language() -> str:
    return _lang


def _(msg: str, **kw) -> str:
    """Translate; named placeholders are filled from the keyword arguments."""
    text = _catalog.get(msg, msg)
    if kw:
        try:
            return text.format(**kw)
        except (KeyError, IndexError, ValueError):
            return msg.format(**kw)          # broken translation: show the original
    return text


set_language(os.environ.get("USBSHARE_LANG") or saved_choice())
