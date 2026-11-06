# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Translation helper – see TRANSLATING.md.

    python tools/i18n.py extract          collect all _("…") texts -> usbshare/locales/usbshare.pot
                                          and update every <code>.po (new texts empty,
                                          removed texts kept as obsolete #~ entries)
    python tools/i18n.py check [--strict] list missing / fuzzy / broken translations
    python tools/i18n.py new fr Français  start a new language from the template
"""
import ast
import glob
import os
import re
import string
import sys
from collections import OrderedDict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from usbshare import __version__  # noqa: E402
from usbshare.i18n import LOCALES, parse_po, po_header  # noqa: E402

POT = os.path.join(LOCALES, "usbshare.pot")
SOURCES = sorted(glob.glob(os.path.join(ROOT, "usbshare", "*.py")))


def quote(s: str) -> str:
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\t", "\\t")
    if "\n" not in s.rstrip("\n") and len(s) < 70:
        return '"' + s.replace("\n", "\\n") + '"'
    parts = s.split("\n")
    lines = [p + "\\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
    return '""\n' + "\n".join('"' + ln + '"' for ln in lines)


def placeholders(s: str):
    return sorted({f for _t, f, _s, _c in string.Formatter().parse(s) if f})


def extract():
    msgs: "OrderedDict[str, list]" = OrderedDict()
    errors = []
    for path in SOURCES:
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        tree = ast.parse(open(path, encoding="utf-8").read(), path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
                if not node.args:
                    continue
                a = node.args[0]
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    msgs.setdefault(a.value, []).append(f"{rel}:{node.lineno}")
                else:
                    errors.append(f"{rel}:{node.lineno}: _() braucht einen festen Text "
                                  "(kein f-String) – Platzhalter als _(\"… {name} …\", name=…)")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        sys.exit(1)
    msgs = OrderedDict(sorted(msgs.items(), key=lambda kv: kv[1][0]))
    return msgs


def header(lang="", name="") -> str:
    h = ("Project-Id-Version: usbshare {v}\\n\nReport-Msgid-Bugs-To: https://github.com/Goeste/usbshare/issues\\n\n"
         "MIME-Version: 1.0\\n\nContent-Type: text/plain; charset=UTF-8\\n\nContent-Transfer-Encoding: 8bit\\n\n"
         "Language: {l}\\n\nX-Language-Name: {n}\\n\nX-Source-Language: de\\n").format(v=__version__, l=lang, n=name)
    return 'msgid ""\nmsgstr ""\n' + "\n".join('"' + ln + '"' for ln in h.split("\n")) + "\n"


def entry(msgid, msgstr, refs, flags=()):
    out = []
    for i in range(0, len(refs), 4):
        out.append("#: " + " ".join(refs[i:i + 4]))
    ph = placeholders(msgid)
    if ph:
        out.append("#. Platzhalter / placeholders: " + ", ".join("{" + p + "}" for p in ph))
    if flags:
        out.append("#, " + ", ".join(flags))
    out.append("msgid " + quote(msgid))
    out.append("msgstr " + quote(msgstr))
    return "\n".join(out) + "\n"


def write_po(path, hdr, msgs, trans, flags, obsolete):
    parts = [hdr]
    for m, refs in msgs.items():
        parts.append(entry(m, trans.get(m, ""), refs, flags.get(m, ())))
    for m, t in obsolete.items():
        parts.append("\n".join("#~ " + ln for ln in entry(m, t, []).splitlines()) + "\n")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(parts))


def read_po(path):
    text = open(path, encoding="utf-8").read()
    entries = parse_po(text)
    trans = {i: s for i, s, _f in entries if i}
    flags = {i: [f for f in fl if f != "python-format"] for i, _s, fl in entries if i and fl}
    # keep the existing header block verbatim (translator names etc.)
    hdr = text.split("\n\n", 1)[0] + "\n"
    old = {}
    for block in text.split("\n\n"):
        lines = [ln[3:] for ln in block.splitlines() if ln.startswith("#~ ")]
        if lines:
            e = parse_po("\n".join(lines))
            if e and e[0][1]:
                old[e[0][0]] = e[0][1]
    return hdr, trans, flags, old, po_header(entries)


def cmd_extract():
    msgs = extract()
    os.makedirs(LOCALES, exist_ok=True)
    write_po(POT, header(), msgs, {}, {}, {})
    print(f"{POT}: {len(msgs)} Texte")
    for po in sorted(glob.glob(os.path.join(LOCALES, "*.po"))):
        hdr, trans, flags, old, h = read_po(po)
        hdr = re.sub(r'"Project-Id-Version: [^"\\]*', f'"Project-Id-Version: usbshare {__version__}', hdr)
        alltrans = {**old, **trans}
        obsolete = {m: t for m, t in alltrans.items() if m not in msgs and t}
        write_po(po, hdr, msgs, alltrans, flags, obsolete)
        missing = sum(1 for m in msgs if not alltrans.get(m))
        print(f"{po}: {len(msgs) - missing}/{len(msgs)} übersetzt, {len(obsolete)} veraltet")


def cmd_check(strict=False):
    msgs = extract()
    bad = 0
    for po in sorted(glob.glob(os.path.join(LOCALES, "*.po"))):
        _hdr, trans, flags, _old, h = read_po(po)
        code = os.path.basename(po)[:-3]
        missing = [m for m in msgs if not trans.get(m)]
        fuzzy = [m for m in msgs if "fuzzy" in flags.get(m, ())]
        broken = [m for m in msgs if trans.get(m) and placeholders(m) != placeholders(trans[m])]
        stale = [m for m in trans if m not in msgs]
        print(f"{code} ({h.get('X-Language-Name', '?')}): {len(msgs) - len(missing)}/{len(msgs)} "
              f"übersetzt, {len(fuzzy)} unsicher (fuzzy), {len(broken)} mit falschen Platzhaltern"
              + (f", {len(stale)} nicht mehr benutzt – bitte 'extract' ausführen" if stale else ""))
        for m in broken:
            print(f"  Platzhalter passen nicht: {m!r} -> {trans[m]!r}")
        if strict:
            for m in missing:
                print(f"  fehlt: {m!r}")
        bad += len(broken) + (len(missing) + len(fuzzy) + len(stale) if strict else 0)
    return 1 if bad else 0


def cmd_new(code, name):
    path = os.path.join(LOCALES, f"{code}.po")
    if os.path.exists(path):
        sys.exit(f"{path} gibt es schon")
    msgs = extract()
    write_po(path, header(code, name), msgs, {}, {}, {})
    print(f"{path} angelegt ({len(msgs)} Texte) – jetzt übersetzen, z. B. mit Poedit.")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["extract"]:
        cmd_extract()
    elif args[:1] == ["check"]:
        sys.exit(cmd_check("--strict" in args))
    elif args[:1] == ["new"] and len(args) == 3:
        cmd_new(args[1], args[2])
    else:
        print(__doc__)
        sys.exit(2)
