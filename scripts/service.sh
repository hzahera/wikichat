#!/usr/bin/env bash
# Run DICE Wiki Chat as a macOS background service (LaunchAgent).
#
#   scripts/service.sh install    start now and at every login (opens the browser)
#   scripts/service.sh uninstall  stop and remove the service
#   scripts/service.sh start | stop | restart | status | logs
#
#   Add --crawl to install to also refresh the wiki index every night at 03:00.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="de.upb.dice.wikichat"
CRAWL_LABEL="de.upb.dice.wikichat.crawl"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
CRAWL_PLIST="$AGENTS/$CRAWL_LABEL.plist"
LOG_DIR="$HOME/Library/Logs/dice-wiki-chat"
DOMAIN="gui/$(id -u)"
PATH_VALUE="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

[ "$(uname)" = "Darwin" ] || { echo "This script is for macOS (launchd)." >&2; exit 1; }

write_server_plist() {
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array><string>/bin/bash</string><string>$ROOT/scripts/start.sh</string></array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$PATH_VALUE</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$LOG_DIR/server.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/server.log</string>
</dict>
</plist>
EOF
}

write_crawl_plist() {
  cat > "$CRAWL_PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$CRAWL_LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string><string>-c</string>
    <string>"$ROOT/.venv/bin/wikichat" crawl &amp;&amp; launchctl kickstart -k $DOMAIN/$LABEL</string>
  </array>
  <key>WorkingDirectory</key><string>$ROOT</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>$PATH_VALUE</string></dict>
  <key>StartCalendarInterval</key><dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>$LOG_DIR/crawl.log</string>
  <key>StandardErrorPath</key><string>$LOG_DIR/crawl.log</string>
</dict>
</plist>
EOF
}

unload() { launchctl bootout "$DOMAIN/$1" >/dev/null 2>&1 || true; }

case "${1:-}" in
  install)
    [ -x "$ROOT/.venv/bin/wikichat" ] || { echo "Run scripts/setup.sh first." >&2; exit 1; }
    mkdir -p "$AGENTS" "$LOG_DIR"
    chmod +x "$ROOT/scripts/start.sh"
    unload "$LABEL"; write_server_plist
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "Service installed. It runs now and at every login."
    if [ "${2:-}" = "--crawl" ]; then
      unload "$CRAWL_LABEL"; write_crawl_plist
      launchctl bootstrap "$DOMAIN" "$CRAWL_PLIST"
      echo "Nightly crawl installed (03:00)."
    fi
    echo "Logs: $LOG_DIR"
    ;;
  uninstall)
    unload "$LABEL"; unload "$CRAWL_LABEL"
    rm -f "$PLIST" "$CRAWL_PLIST"
    echo "Service removed."
    ;;
  start)   launchctl kickstart "$DOMAIN/$LABEL" && echo "Started." ;;
  stop)    launchctl kill TERM "$DOMAIN/$LABEL" && echo "Stopped (it starts again at next login)." ;;
  restart) launchctl kickstart -k "$DOMAIN/$LABEL" && echo "Restarted." ;;
  status)
    if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
      launchctl print "$DOMAIN/$LABEL" | grep -E "^\s+(state|pid|last exit code)" || true
    else
      echo "Not installed."
    fi
    launchctl print "$DOMAIN/$CRAWL_LABEL" >/dev/null 2>&1 && echo "Nightly crawl: installed" || true
    ;;
  logs) tail -n 50 -f "$LOG_DIR/server.log" ;;
  *)
    sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
