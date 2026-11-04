@echo off
rem Server einmalig im Vordergrund starten (zum Ausprobieren, ohne Installation).
rem Danach usbshare-server-gui.exe oeffnen, um Geraete freizugeben und zuzuweisen.
rem Fenster offen lassen, solange die Geraete genutzt werden. Beenden mit Strg+C.
cd /d "%~dp0"
net session >nul 2>&1 || (echo Bitte per Rechtsklick "Als Administrator ausfuehren". & pause & exit /b 1)
start "" usbshare-server-gui.exe
usbshare.exe serve --config usbshare.toml
pause
