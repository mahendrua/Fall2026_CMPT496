#!/usr/bin/env bash
set -euo pipefail

Xvfb :99 -screen 0 1280x800x24 -nolisten tcp -noreset -ac &
display_ready=false
for attempt in {1..100}; do
    if xdpyinfo -display :99 >/dev/null 2>&1; then display_ready=true; break; fi
    sleep 0.1
done
if [ "$display_ready" != true ]; then echo "Browser display failed to start" >&2; exit 1; fi
x11vnc -display :99 -rfbport 5900 -localhost -forever -shared -passwd "${VNC_PASSWORD}" &
unset VNC_PASSWORD
fluxbox -display :99 &
websockify --web=/usr/share/novnc 6080 localhost:5900 &

exec node /opt/gui-runner/runner.js
