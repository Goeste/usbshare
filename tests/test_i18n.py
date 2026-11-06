# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Translations: .po parsing, language choice, completeness of the shipped catalogs."""
import glob
import importlib.util
import os
import sys
import tempfile
import unittest
from unittest import mock

from usbshare import i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def tool():
    spec = importlib.util.spec_from_file_location("i18n_tool", os.path.join(ROOT, "tools", "i18n.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PO = r'''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\n"
"X-Language-Name: Testisch\n"

#: a.py:1
msgid "Hallo {name}"
msgstr "Hi {name}"

#, fuzzy
msgid "Unsicher"
msgstr "Unsure"

msgid ""
"Zwei\n"
"Zeilen"
msgstr ""
"Two\n"
"lines"

msgid "Leer"
msgstr ""

msgid "Kaputt {x}"
msgstr "Broken {y}"

#~ msgid "Alt"
#~ msgstr "Old"
'''


class Catalog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        with open(os.path.join(self.dir, "tt.po"), "w", encoding="utf-8") as f:
            f.write(PO)
        self.p = mock.patch.object(i18n, "LOCALES", self.dir)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        i18n.set_language("de")

    def test_parse(self):
        cat = i18n.load_catalog(os.path.join(self.dir, "tt.po"))
        self.assertEqual(cat["Hallo {name}"], "Hi {name}")
        self.assertEqual(cat["Zwei\nZeilen"], "Two\nlines")
        self.assertNotIn("Unsicher", cat)            # fuzzy entries are not used
        self.assertNotIn("Leer", cat)                # untranslated -> German text
        self.assertNotIn("Alt", cat)                 # obsolete

    def test_language_list_and_switch(self):
        self.assertEqual(i18n.available(), {"de": "Deutsch", "tt": "Testisch"})
        self.assertEqual(i18n.set_language("tt"), "tt")
        self.assertEqual(i18n._("Hallo {name}", name="Ada"), "Hi Ada")
        self.assertEqual(i18n._("Leer"), "Leer")
        self.assertEqual(i18n._("Kaputt {x}", x=1), "Kaputt 1")   # broken translation: original

    def test_auto_uses_system_language(self):
        with mock.patch.object(i18n, "system_language", return_value="tt"):
            self.assertEqual(i18n.resolve("auto"), "tt")
        with mock.patch.object(i18n, "system_language", return_value="de"):
            self.assertEqual(i18n.resolve("auto"), "de")

    def test_unknown_language_falls_back(self):
        with mock.patch.object(i18n, "system_language", return_value="xx"):
            self.assertEqual(i18n.resolve("auto"), "de")     # no en.po in this test folder
        self.assertEqual(i18n.resolve("tt_TT"), "tt")

    def test_saved_choice(self):
        with mock.patch.object(i18n, "config_dir", return_value=self.dir):
            self.assertEqual(i18n.saved_choice(), "auto")
            i18n.save_choice("tt")
            self.assertEqual(i18n.saved_choice(), "tt")


class SystemLanguage(unittest.TestCase):
    @unittest.skipIf(sys.platform.startswith("win"), "environment based")
    def test_from_environment(self):
        with mock.patch.dict(os.environ, {"LC_ALL": "", "LC_MESSAGES": "", "LANG": "en_US.UTF-8"}):
            self.assertEqual(i18n.system_language(), "en")
        with mock.patch.dict(os.environ, {"LC_ALL": "C.UTF-8", "LC_MESSAGES": "", "LANG": "de_DE.UTF-8"}):
            self.assertEqual(i18n.system_language(), "de")


class ShippedTranslations(unittest.TestCase):
    """Every text marked with _() must be in the catalogs; English must be complete."""

    def test_catalogs_up_to_date_and_valid(self):
        t = tool()
        msgs = t.extract()
        pos = glob.glob(os.path.join(i18n.LOCALES, "*.po"))
        self.assertIn(os.path.join(i18n.LOCALES, "en.po"), pos)
        for po in pos:
            _hdr, trans, _flags, _old, header = t.read_po(po)
            self.assertTrue(header.get("X-Language-Name"), po)
            missing_in_file = [m for m in msgs if m not in trans]
            self.assertEqual(missing_in_file, [], f"{po}: run python tools/i18n.py extract")
            for m, s in trans.items():
                if s:
                    self.assertEqual(t.placeholders(m), t.placeholders(s), f"{po}: {m!r}")

    def test_english_complete(self):
        t = tool()
        _hdr, trans, flags, _old, _h = t.read_po(os.path.join(i18n.LOCALES, "en.po"))
        self.assertEqual([m for m in t.extract() if not trans.get(m)], [])
        self.assertEqual([m for m, f in flags.items() if "fuzzy" in f], [])

    def test_english_ui(self):
        try:
            i18n.set_language("en")
            from usbshare.gui_model import device_label
            self.assertEqual(device_label({"class": 3}), "USB device (Input device (HID))")
            from usbshare.config import ServerConfig
            self.assertIn("# usbshare server configuration", ServerConfig().render())
        finally:
            i18n.set_language("de")


if __name__ == "__main__":
    unittest.main()
