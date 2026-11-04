#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare
# usbshare-Server installieren (Rechner, an dem die USB-Geräte stecken)
#   sudo ./install.sh                       alle Clients erlaubt
#   sudo ./install.sh 192.168.1.50          nur diese VM darf sich verbinden
#   sudo ./install.sh 192.168.1.50 192.168.1.51
#   sudo ./install.sh --uninstall
set -e
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen"; exit 1; }

if [ "${1:-}" = "--uninstall" ]; then
    systemctl disable --now usbshare 2>/dev/null || true
    rm -f /etc/systemd/system/usbshare.service /usr/local/bin/usbshare \
          /usr/share/applications/usbshare-server.desktop
    systemctl daemon-reload
    echo "usbshare entfernt (Konfiguration in /etc/usbshare bleibt erhalten)."
    exit 0
fi

if ! ldconfig -p | grep -q 'libusb-1.0.so'; then
    echo "Installiere libusb …"
    if command -v apt-get >/dev/null; then apt-get install -y libusb-1.0-0
    elif command -v dnf >/dev/null; then dnf install -y libusb1
    elif command -v pacman >/dev/null; then pacman -S --noconfirm libusb
    else echo "Bitte libusb-1.0 selbst installieren."; exit 1; fi
fi

install -m 755 usbshare /usr/local/bin/usbshare
if ! /usr/local/bin/usbshare --version >/dev/null 2>&1; then
    echo "FEHLER: Das Programm startet auf diesem System nicht (zu alte glibc?)."
    echo "Benötigt etwa Ubuntu 24.04 / Debian 13 oder neuer."
    exit 1
fi

mkdir -p /etc/usbshare
[ -f /etc/usbshare/usbshare.toml ] || install -m 644 usbshare.toml /etc/usbshare/usbshare.toml
if [ $# -gt 0 ]; then
    list=$(printf '"%s", ' "$@"); list="[${list%, }]"
    sed -i "s|^allow_clients = .*|allow_clients = $list|" /etc/usbshare/usbshare.toml
fi
install -m 644 usbshare.service /etc/systemd/system/usbshare.service
systemctl daemon-reload
systemctl enable usbshare >/dev/null
systemctl restart usbshare

if command -v ufw >/dev/null && ufw status | grep -q active; then
    ufw allow 3240/tcp >/dev/null && ufw allow 3240/udp >/dev/null && \
        echo "Firewall (ufw): Port 3240 TCP+UDP freigegeben"
elif command -v firewall-cmd >/dev/null && firewall-cmd --state >/dev/null 2>&1; then
    firewall-cmd --permanent --add-port=3240/tcp --add-port=3240/udp >/dev/null && \
        firewall-cmd --reload >/dev/null && echo "Firewall (firewalld): Port 3240 TCP+UDP freigegeben"
fi

# Menü-Eintrag für das Server-Fenster (Desktop-Systeme)
if [ -d /usr/share/applications ]; then
    cat > /usr/share/applications/usbshare-server.desktop <<'DESK'
[Desktop Entry]
Type=Application
Name=usbshare Server
Comment=USB-Geräte freigeben und Clients zuweisen
Exec=/usr/local/bin/usbshare gui
Icon=drive-removable-media
Terminal=false
Categories=System;Settings;
DESK
fi

echo
echo "Fertig – der Server läuft und startet automatisch mit dem System."
echo
/usr/local/bin/usbshare list || true
echo
echo "  Geräte freigeben/zuweisen: Menü -> 'usbshare Server' oder 'usbshare gui' (ohne sudo)"
echo "  Konfiguration: /etc/usbshare/usbshare.toml  (Änderungen werden automatisch übernommen)"
echo "  Log:           journalctl -u usbshare -f"
echo "  IP-Adressen dieses Rechners: $(hostname -I 2>/dev/null)"
