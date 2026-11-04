#!/bin/bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Goeste – usbshare
# usbshare-attach – läuft in der Linux-VM, wartet auf USB-Geräte vom usbshare-Server
# und bindet sie automatisch ein (auch nach Abstecken/Neustart des Servers).
# Konfiguration: /etc/usbshare-attach.conf
set -u

CONF=${USBSHARE_CONF:-/etc/usbshare-attach.conf}
SERVER=""       # ein oder mehrere Server, mit Leerzeichen getrennt; optional mit :PORT
PORT=3240
DEVICES=""      # leer = alle Geräte; sonst z. B. "046d:c52b 1-4 0781:*"
INTERVAL=3
[ -f "$CONF" ] && . "$CONF"

log() { echo "$*"; }

if [ -z "$SERVER" ]; then
    log "FEHLER: SERVER ist in $CONF nicht gesetzt"; exit 1
fi
USBIP=$(command -v usbip || echo /usr/sbin/usbip)
if [ ! -x "$USBIP" ]; then
    log "FEHLER: 'usbip' nicht gefunden. Debian: apt install usbip / Ubuntu: apt install linux-tools-generic"
    exit 1
fi

wanted() {  # $1=busid $2=vid $3=pid
    [ -z "$DEVICES" ] && return 0
    local rule
    for rule in $DEVICES; do
        rule=${rule,,}
        if [[ "$rule" == *:* ]]; then
            local v=${rule%%:*} p=${rule#*:}
            [[ ( "$v" == "*" || "$v" == "$2" ) && ( "$p" == "*" || "$p" == "$3" ) ]] && return 0
        elif [ "$rule" == "$1" ]; then
            return 0
        fi
    done
    return 1
}

detach_all() {
    local p
    for p in $("$USBIP" port 2>/dev/null | sed -n 's/^Port \([0-9]*\):.*/\1/p'); do
        "$USBIP" detach -p "$p" >/dev/null 2>&1 && log "Port $p getrennt"
    done
}

if [ "${1:-}" = "stop" ]; then detach_all; exit 0; fi

modprobe vhci-hcd 2>/dev/null
log "warte auf Geräte von: $SERVER (Filter: ${DEVICES:-alle})"
declare -A online=()

poll() {  # $1 = host oder host:port
    local host=$1 port=$PORT
    if [[ "$1" =~ ^(.+):([0-9]+)$ && "$1" != *::* ]]; then host=${BASH_REMATCH[1]}; port=${BASH_REMATCH[2]}; fi
    local portopt=()
    [ "$port" != "3240" ] && portopt=(--tcp-port "$port")
    local out res busid vid pid
    if out=$("$USBIP" "${portopt[@]}" list -r "$host" 2>&1); then
        [ "${online[$1]:-}" != yes ] && log "Server $1 erreichbar"; online[$1]=yes
        # Zeilen wie: "        1-4.2: Logitech, Inc. : Unifying Receiver (046d:c52b)"
        while read -r busid vid pid; do
            wanted "$busid" "$vid" "$pid" || continue
            if res=$("$USBIP" "${portopt[@]}" attach -r "$host" -b "$busid" 2>&1); then
                log "eingebunden: $busid ($vid:$pid) von $1"
            else
                log "einbinden von $busid ($1) fehlgeschlagen: $res"
            fi
        done < <(sed -nE 's/^[[:space:]]+([0-9]+-[0-9a-z.]+): .*\(([0-9a-fA-F]{4}):([0-9a-fA-F]{4})\)[[:space:]]*$/\1 \2 \3/p' <<<"$out" | tr 'A-F' 'a-f')
    else
        [ "${online[$1]:-}" != no ] && log "Server $1 nicht erreichbar, versuche weiter …"; online[$1]=no
    fi
}

while true; do
    for srv in $SERVER; do poll "$srv"; done
    sleep "$INTERVAL"
done
