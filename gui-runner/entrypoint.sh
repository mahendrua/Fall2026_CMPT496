#!/usr/bin/env bash
set -euo pipefail

Xvfb :99 -screen 0 1280x800x24 -nolisten tcp -noreset -ac &
x11vnc -display :99 -rfbport 5900 -localhost -forever -shared -passwd "${VNC_PASSWORD}" &
unset VNC_PASSWORD
fluxbox -display :99 &
websockify --web=/usr/share/novnc 6080 localhost:5900 &

exec node /opt/gui-runner/runner.js
