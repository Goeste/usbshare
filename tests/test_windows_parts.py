# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare (siehe LICENSE und NOTICE)
"""Release package selection, usbip-win2 installation, Windows server update, packaging."""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from unittest import mock

from usbshare import cli, driver
from usbshare import updater as U

from tests.test_updater import fake_release, make_zip

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PackageSelection(unittest.TestCase):
    NAMES = ["usbshare-server-0.7.0-linux.zip", "usbshare-windows-server-0.7.0.zip",
             "usbshare-client-0.7.0-windows.zip", "SHA256SUMS.txt"]

    def rel(self, names):
        return U.Release("0.7.0", "", "", {n: {"url": n} for n in names})

    def test_new_names(self):
        r = self.rel(self.NAMES)
        self.assertEqual(r.asset("client"), "usbshare-client-0.7.0-windows.zip")
        self.assertEqual(r.asset("server-linux"), "usbshare-server-0.7.0-linux.zip")
        self.assertEqual(r.asset("server-windows"), "usbshare-windows-server-0.7.0.zip")

    def test_server_follows_platform(self):
        r = self.rel(self.NAMES)
        with mock.patch.object(U, "IS_WIN", True):
            self.assertEqual(r.asset("server"), "usbshare-windows-server-0.7.0.zip")
        with mock.patch.object(U, "IS_WIN", False):
            self.assertEqual(r.asset("server"), "usbshare-server-0.7.0-linux.zip")

    def test_old_updaters_still_find_the_linux_package(self):
        # 0.6.x picks the first asset starting with "usbshare-server-"
        old = [n for n in self.NAMES if n.startswith("usbshare-server-") and n.endswith(".zip")]
        self.assertEqual(old, ["usbshare-server-0.7.0-linux.zip"])

    def test_combined_package_of_old_releases(self):
        r = self.rel(["usbshare-server-0.6.4.zip"])
        with mock.patch.object(U, "IS_WIN", False):
            self.assertEqual(r.asset("server"), "usbshare-server-0.6.4.zip")
        with mock.patch.object(U, "IS_WIN", True):
            self.assertIsNone(r.asset("server"))


class DriverChoice(unittest.TestCase):
    def inst(self, v, src):
        return driver.Installer(v, src)

    def test_not_installed_prefers_bundled_when_equal(self):
        got = driver.choose(None, self.inst("0.9.8.1", "bundled"), self.inst("0.9.8.1", "github"))
        self.assertEqual(got.source, "bundled")

    def test_newer_online_wins(self):
        got = driver.choose(None, self.inst("0.9.8.0", "bundled"), self.inst("0.9.8.1", "github"))
        self.assertEqual((got.source, got.version), ("github", "0.9.8.1"))

    def test_offline_uses_bundled(self):
        self.assertEqual(driver.choose(None, self.inst("0.9.8.1", "bundled"), None).source, "bundled")

    def test_up_to_date(self):
        self.assertIsNone(driver.choose("0.9.8.1", self.inst("0.9.8.1", "bundled"),
                                        self.inst("0.9.8.1", "github")))

    def test_update_available(self):
        self.assertEqual(driver.choose("0.9.7.7", None, self.inst("0.9.8.1", "github")).version,
                         "0.9.8.1")

    def test_unknown_version_is_left_alone(self):
        self.assertIsNone(driver.choose("", self.inst("0.9.8.1", "bundled"), None))

    def test_nothing_available(self):
        self.assertIsNone(driver.choose(None, None, None))


class DriverFiles(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def put(self, name, data=b"MZ setup"):
        with open(os.path.join(self.dir, name), "wb") as f:
            f.write(data)
        return hashlib.sha256(data).hexdigest()

    def test_bundled_picks_newest_and_verifies(self):
        self.put("USBip-0.9.7.7-x64-Release.exe", b"old")
        h = self.put("USBip-0.9.8.1-x64-Release.exe")
        self.put("readme.exe")
        with open(os.path.join(self.dir, "SHA256SUMS.txt"), "w") as f:
            f.write(f"{h}  USBip-0.9.8.1-x64-Release.exe\n")
        b = driver.bundled(self.dir)
        self.assertEqual((b.version, b.sha256), ("0.9.8.1", h))
        self.assertEqual(driver.fetch(b), b.path)
        with open(b.path, "ab") as f:
            f.write(b"tampered")
        with self.assertRaises(U.UpdateError):
            driver.fetch(b)

    def test_no_bundle(self):
        self.assertIsNone(driver.bundled(self.dir))

    def test_latest_online(self):
        data = {"assets": [
            {"name": "USBip-0.9.8.1-x64-Debug.exe", "browser_download_url": "d", "digest": "sha256:aa"},
            {"name": "USBip-0.9.8.1-ARM64-Release.exe", "browser_download_url": "a", "digest": "sha256:bb"},
            {"name": "USBip-0.9.8.1-x64-Release.exe", "browser_download_url": "u",
             "digest": "sha256:CC", "size": 5}]}
        with mock.patch.object(U, "_get", return_value=json.dumps(data).encode()):
            i = driver.latest_online()
        self.assertEqual((i.version, i.url, i.sha256, i.source), ("0.9.8.1", "u", "cc", "github"))

    def test_online_without_digest_is_not_offered(self):
        data = {"assets": [{"name": "USBip-0.9.8.1-x64-Release.exe", "browser_download_url": "u"}]}
        with mock.patch.object(U, "_get", return_value=json.dumps(data).encode()):
            self.assertIsNone(driver.latest_online())

    def test_offline(self):
        with mock.patch.object(U, "_get", side_effect=U.UpdateError("offline")):
            self.assertIsNone(driver.latest_online())

    def test_download_is_verified(self):
        i = driver.Installer("0.9.8.1", "github", url="u", sha256=hashlib.sha256(b"MZ").hexdigest())
        with mock.patch.object(U, "_get", return_value=b"MZ"):
            p = driver.fetch(i)
        self.assertEqual(open(p, "rb").read(), b"MZ")
        with mock.patch.object(U, "_get", return_value=b"evil"), self.assertRaises(U.UpdateError):
            driver.fetch(i)

    def test_silent_install(self):
        with mock.patch.object(driver.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 0)) as run:
            driver.run_installer("C:/x/USBip.exe")
        args = run.call_args[0][0]
        self.assertEqual(args[0], "C:/x/USBip.exe")
        for flag in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART"):
            self.assertIn(flag, args)
        with mock.patch.object(driver.subprocess, "run",
                               return_value=subprocess.CompletedProcess([], 7)), \
                self.assertRaises(U.UpdateError):
            driver.run_installer("C:/x/USBip.exe")


class WindowsServerUpdate(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.exe = os.path.join(self.dir, "usbshare.exe")
        self.gui = os.path.join(self.dir, "usbshare-server-gui.exe")
        for p in (self.exe, self.gui):
            with open(p, "wb") as f:
                f.write(b"MZ old")

    def _run(self, starts=True):
        z = make_zip({"usbshare-windows-server/usbshare.exe": b"MZ new server",
                      "usbshare-windows-server/usbshare-server-gui.exe": b"MZ new gui"})
        rel, get = fake_release("9.9.9", {"usbshare-windows-server-9.9.9.zip": z})
        args = cli.argparse.Namespace(check=False, yes=True, force=False, report=None)
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0)
        with mock.patch.object(U, "latest_release", return_value=rel), \
                mock.patch.object(U, "_get", get), \
                mock.patch.object(U, "IS_WIN", True), \
                mock.patch.object(cli, "IS_WIN", True), \
                mock.patch.object(cli, "_is_admin", return_value=True), \
                mock.patch.object(cli, "_starts", return_value=starts), \
                mock.patch.object(U, "frozen_executable", return_value=self.exe), \
                mock.patch.object(subprocess, "run", fake_run):
            rc = cli.cmd_update(args)
        return rc, calls

    def read(self, p):
        with open(p, "rb") as f:
            return f.read()

    def test_replaces_both_programs_and_restarts_task(self):
        rc, calls = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(self.read(self.exe), b"MZ new server")
        self.assertEqual(self.read(self.gui), b"MZ new gui")
        self.assertEqual(self.read(self.exe + ".old"), b"MZ old")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "usbshare-new.exe")))
        flat = [" ".join(c) for c in calls]
        self.assertTrue(any("schtasks /End /TN usbshare" in c for c in flat))
        self.assertTrue(any("taskkill" in c and f"PID ne {os.getpid()}" in c for c in flat))
        self.assertTrue(flat[-1].startswith("schtasks /Run /TN usbshare"))

    def test_broken_new_version_keeps_old_one(self):
        rc, calls = self._run(starts=False)
        self.assertEqual(rc, 2)
        self.assertEqual(self.read(self.exe), b"MZ old")
        self.assertFalse(os.path.exists(os.path.join(self.dir, "usbshare-new.exe")))
        self.assertFalse(any("taskkill" in " ".join(c) for c in calls))   # server kept running

    def test_needs_admin(self):
        args = cli.argparse.Namespace(check=False, yes=True, force=False, report=None)
        rel, _ = fake_release("9.9.9", {})
        with mock.patch.object(U, "latest_release", return_value=rel), \
                mock.patch.object(cli, "IS_WIN", True), \
                mock.patch.object(cli, "_is_admin", return_value=False), \
                mock.patch.object(U, "frozen_executable", return_value=self.exe):
            self.assertEqual(cli.cmd_update(args), 1)


class Packages(unittest.TestCase):
    def test_three_packages(self):
        bins, usbip, out = (tempfile.mkdtemp() for _ in range(3))
        for n in ("usbshare-linux-x86_64", "usbshare.exe", "usbshare-server-gui.exe",
                  "usbshare-client.exe"):
            with open(os.path.join(bins, n), "wb") as f:
                f.write(b"bin")
        with open(os.path.join(usbip, "USBip-0.9.8.1-x64-Release.exe"), "wb") as f:
            f.write(b"setup")
        with open(os.path.join(usbip, "LICENSE.txt"), "w") as f:
            f.write("BSD 2-Clause")
        subprocess.run([sys.executable, os.path.join(ROOT, "packaging", "make_packages.py"),
                        "--version", "9.9.9", "--bins", bins, "--out", out, "--usbip", usbip],
                       check=True, capture_output=True)
        self.assertEqual(sorted(os.listdir(out)), [
            "usbshare-client-9.9.9-windows.zip", "usbshare-server-9.9.9-linux.zip",
            "usbshare-windows-server-9.9.9.zip"])
        names = lambda z: set(zipfile.ZipFile(os.path.join(out, z)).namelist())  # noqa: E731
        lin = names("usbshare-server-9.9.9-linux.zip")
        self.assertIn("usbshare-server/linux/usbshare", lin)
        self.assertFalse([n for n in lin if n.endswith(".exe")])
        win = names("usbshare-windows-server-9.9.9.zip")
        for n in ("usbshare.exe", "usbshare-server-gui.exe", "install-service.ps1", "usbshare.toml",
                  "LICENSE", "NOTICE"):
            self.assertIn(f"usbshare-windows-server/{n}", win)
        cl = names("usbshare-client-9.9.9-windows.zip")
        for n in ("usbshare-client.exe", "usbip-win2/USBip-0.9.8.1-x64-Release.exe",
                  "usbip-win2/LICENSE.txt", "usbip-win2/SHA256SUMS.txt"):
            self.assertIn(f"usbshare-client/{n}", cl)
        # the client finds and verifies the bundled installer
        d = tempfile.mkdtemp()
        zipfile.ZipFile(os.path.join(out, "usbshare-client-9.9.9-windows.zip")).extractall(d)
        b = driver.bundled(os.path.join(d, "usbshare-client", "usbip-win2"))
        self.assertEqual(b.version, "0.9.8.1")
        self.assertEqual(driver.fetch(b), b.path)


if __name__ == "__main__":
    unittest.main()
