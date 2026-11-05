# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Updater: version compare, checksum verification, Linux server self-update."""
import hashlib
import io
import os
import stat
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from usbshare import cli
from usbshare import updater as U


def make_zip(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def fake_release(version, zips, sums=True, digest=False, corrupt=False):
    assets, blobs = {}, {}
    lines = []
    for name, data in zips.items():
        url = f"https://example.invalid/{name}"
        h = hashlib.sha256(data).hexdigest()
        assets[name] = {"url": url, "size": len(data), "digest": f"sha256:{h}" if digest else ""}
        blobs[url] = data + (b"x" if corrupt else b"")
        lines.append(f"{h}  {name}")
    if sums:
        assets[U.SUMS_NAME] = {"url": "https://example.invalid/sums", "size": 0, "digest": ""}
        blobs["https://example.invalid/sums"] = ("\n".join(lines) + "\n").encode()
    rel = U.Release(version, "### Behoben\n- **Etwas** repariert\n\n**Downloads:** …",
                    "https://example.invalid/rel", assets)
    return rel, (lambda url, **kw: blobs[url])


class VersionTest(unittest.TestCase):
    def test_compare(self):
        self.assertTrue(U.is_newer("0.4.10", "0.4.9"))
        self.assertTrue(U.is_newer("v1.0.0", "0.9.9"))
        self.assertFalse(U.is_newer("0.4.7", "0.4.7"))
        self.assertFalse(U.is_newer("0.4.6", "0.4.7"))

    def test_notes_are_shortened(self):
        self.assertEqual(U.short_notes("### Behoben\n- **A** `b`\n\n**Downloads:** x"),
                         "Behoben\n- A b")
        self.assertEqual(U.short_notes("- erste Zeile\n  weiter\n- zweite"),
                         "- erste Zeile weiter\n- zweite")


class DownloadTest(unittest.TestCase):
    def test_checksum_from_sums_file(self):
        z = make_zip({"usbshare-client/usbshare-client.exe": b"MZ new"})
        rel, get = fake_release("9.9.9", {"usbshare-client-9.9.9-windows.zip": z})
        with mock.patch.object(U, "_get", get):
            zf = U.download_package(rel, "client")
        self.assertEqual(U.extract_member(zf, "/usbshare-client.exe"), b"MZ new")

    def test_checksum_from_asset_digest(self):
        z = make_zip({"a": b"1"})
        rel, get = fake_release("9.9.9", {"usbshare-server-9.9.9.zip": z}, sums=False, digest=True)
        with mock.patch.object(U, "_get", get):
            U.download_package(rel, "server")

    def test_corrupt_download_is_rejected(self):
        z = make_zip({"a": b"1"})
        rel, get = fake_release("9.9.9", {"usbshare-server-9.9.9.zip": z}, corrupt=True)
        with mock.patch.object(U, "_get", get), self.assertRaises(U.UpdateError):
            U.download_package(rel, "server")

    def test_missing_checksum_is_rejected(self):
        z = make_zip({"a": b"1"})
        rel, get = fake_release("9.9.9", {"usbshare-server-9.9.9.zip": z}, sums=False)
        with mock.patch.object(U, "_get", get), self.assertRaises(U.UpdateError):
            U.download_package(rel, "server")


@unittest.skipIf(sys.platform.startswith("win"), "Linux server update")
class LinuxServerUpdate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.target = os.path.join(self.dir, "usbshare")
        with open(self.target, "w") as f:
            f.write("#!/bin/sh\necho 'usbshare 0.0.1'\n")
        os.chmod(self.target, 0o755)

    def _run(self, new_binary):
        z = make_zip({"usbshare-server/linux/usbshare": new_binary,
                      "usbshare-server/linux/usbshare.service": b"[Unit]\n"})
        rel, get = fake_release("9.9.9", {"usbshare-server-9.9.9.zip": z})
        args = cli.argparse.Namespace(check=False, yes=True, force=False)
        with mock.patch.object(U, "latest_release", return_value=rel), \
                mock.patch.object(U, "_get", get), \
                mock.patch.object(U, "frozen_executable", return_value=self.target), \
                mock.patch.object(cli.os, "geteuid", return_value=0), \
                mock.patch("shutil.which", return_value=None):
            return cli.cmd_update(args)

    def test_replaces_binary(self):
        rc = self._run(b"#!/bin/sh\necho 'usbshare 9.9.9'\n")
        self.assertEqual(rc, 0)
        self.assertIn(b"9.9.9", open(self.target, "rb").read())
        self.assertTrue(os.stat(self.target).st_mode & stat.S_IXUSR)
        self.assertFalse(os.path.exists(self.target + ".new"))

    def test_broken_new_binary_keeps_old_one(self):
        rc = self._run(b"#!/bin/sh\nexit 1\n")
        self.assertEqual(rc, 2)
        self.assertIn(b"0.0.1", open(self.target, "rb").read())
        self.assertFalse(os.path.exists(self.target + ".new"))

    def test_check_only(self):
        rel, _ = fake_release("9.9.9", {})
        args = cli.argparse.Namespace(check=True, yes=False, force=False)
        with mock.patch.object(U, "latest_release", return_value=rel):
            self.assertEqual(cli.cmd_update(args), 10)


class WindowsExeSwap(unittest.TestCase):
    """The client replaces its own running .exe (rename old, put new in place, start it)."""

    def test_swap(self):
        try:
            from usbshare import update_ui
        except ImportError as e:            # no tkinter in this environment
            raise unittest.SkipTest(str(e))
        d = tempfile.mkdtemp()
        exe = os.path.join(d, "usbshare-client.exe")
        open(exe, "wb").write(b"MZ old")
        z = make_zip({"usbshare-client/usbshare-client.exe": b"MZ new"})
        rel, get = fake_release("9.9.9", {"usbshare-client-9.9.9-windows.zip": z})
        with mock.patch.object(U, "_get", get), \
                mock.patch.object(U, "frozen_executable", return_value=exe), \
                mock.patch.object(update_ui.subprocess, "Popen") as popen:
            update_ui.install_windows_exe(rel, "client", "/usbshare-client.exe", lambda a, b: None)
        self.assertEqual(open(exe, "rb").read(), b"MZ new")
        self.assertEqual(open(exe + ".old", "rb").read(), b"MZ old")
        self.assertEqual(popen.call_args[0][0], [exe, "--after-update"])
        with mock.patch.object(U, "frozen_executable", return_value=exe):
            update_ui.cleanup_old_exe()
        self.assertFalse(os.path.exists(exe + ".old"))


if __name__ == "__main__":
    unittest.main()
