#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare
# Installation in der Linux-VM:   sudo ./install.sh 192.168.1.20
set -e
cd "$(dirname "$0")"
[ "$(id -u)" = 0 ] || { echo "Bitte mit sudo ausführen"; exit 1; }

if ! command -v usbip >/dev/null && [ ! -x /usr/sbin/usbip ]; then
    echo "Installiere usbip …"
    if grep -qi ubuntu /etc/os-release; then
        apt-get install -y linux-tools-generic "linux-tools-$(uname -r)" || apt-get install -y linux-tools-generic
    elif command -v apt-get >/dev/null; then
        apt-get install -y usbip
    elif command -v dnf >/dev/null; then
        dnf install -y usbip
    elif command -v pacman >/dev/null; then
        pacman -S --noconfirm usbip
    else
        echo "Bitte das Paket 'usbip' selbst installieren."; exit 1
    fi
fi

echo vhci-hcd > /etc/modules-load.d/vhci-hcd.conf
modprobe vhci-hcd

install -m 755 usbshare-attach.sh /usr/local/bin/usbshare-attach.sh
if [ ! -f /etc/usbshare-attach.conf ]; then
    install -m 644 usbshare-attach.conf /etc/usbshare-attach.conf
fi
if [ $# -gt 0 ]; then
    sed -i "s/^SERVER=.*/SERVER=\"$*\"/" /etc/usbshare-attach.conf
fi
install -m 644 usbshare-attach.service /etc/systemd/system/usbshare-attach.service
systemctl daemon-reload
systemctl enable --now usbshare-attach.service

echo
echo "Fertig. Der Dienst startet ab jetzt bei jedem Boot."
echo "  Einstellungen: /etc/usbshare-attach.conf  (danach: systemctl restart usbshare-attach)"
echo "  Log ansehen:   journalctl -u usbshare-attach -f"
echo "  Eingebunden:   usbip port"
