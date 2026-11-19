#!/bin/bash
# SensIR-Wächter für die Synology - läuft AUSSERHALB von Docker.
#
# Hintergrund: Am 2026-11-17 und 2026-11-19 ist der Docker-Dienst der NAS
# stehen geblieben (Container Manager "gestoppt"), SensIR war stundenlang weg,
# bis jemand den Container Manager von Hand neu gestartet hat. Autoheal kann
# das nicht abfangen, weil es selbst ein Docker-Container ist.
#
# Ablauf (vom DSM-Aufgabenplaner alle 5 Minuten als root gestartet):
#   - prüft http://127.0.0.1:8000/healthz
#   - antwortet SensIR FAIL_MINUTES lang nicht: Telegram-Meldung, dann
#     "synopkg restart ContainerManager", danach bis zu WAIT_AFTER_RESTART
#     Sekunden warten und Erfolg oder Misserfolg per Telegram melden
#   - höchstens ein Neustart pro COOLDOWN_MINUTES, höchstens MAX_RESTARTS
#     Versuche pro Störung; danach nur noch Meldung "bitte von Hand prüfen"
#   - meldet, wenn SensIR wieder antwortet
#
# Telegram-Zugangsdaten kommen aus SensIRs .env (TELEGRAM_BOT_TOKEN,
# ADMIN_TELEGRAM_CHAT_ID) und werden direkt an api.telegram.org geschickt -
# das funktioniert auch, wenn Docker steht.
#
# Aufrufe:
#   nas-watchdog.sh                 normaler Lauf (Aufgabenplaner)
#   nas-watchdog.sh --dry-run       prüft und protokolliert, startet nichts neu,
#                                   sendet nichts, ändert keinen Zustand
#   nas-watchdog.sh --test-telegram sendet eine Testnachricht

set -u

SENSIR_DIR="${SENSIR_DIR:-/volume1/docker/sensir}"
ENV_FILE="${ENV_FILE:-$SENSIR_DIR/.env}"
STATE_DIR="${STATE_DIR:-$SENSIR_DIR/ops/watchdog-state}"
STATE_FILE="$STATE_DIR/state"
LOG_FILE="$STATE_DIR/watchdog.log"
LOCK_FILE="$STATE_DIR/lock"

HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/healthz}"
SYNOPKG="${SYNOPKG:-/usr/syno/bin/synopkg}"
FAIL_MINUTES="${FAIL_MINUTES:-15}"          # Autoheal braucht selbst ~5-7 Min
COOLDOWN_MINUTES="${COOLDOWN_MINUTES:-60}"
MAX_RESTARTS="${MAX_RESTARTS:-3}"
WAIT_AFTER_RESTART="${WAIT_AFTER_RESTART:-300}"

MODE="run"
case "${1:-}" in
  --dry-run) MODE="dry" ;;
  --test-telegram) MODE="test" ;;
  "") ;;
  *) echo "Unbekannte Option: $1" >&2; exit 2 ;;
esac

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

log() {
  echo "$(date '+%F %T') [$MODE] $*" >> "$LOG_FILE"
  if [ -t 1 ]; then echo "$*"; fi
  return 0
}

# Nur die zwei benötigten Werte lesen, die .env NICHT ausführen.
env_value() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" 2>/dev/null \
    | tail -n 1 | sed -e 's/[[:space:]]*$//' -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

telegram() {
  local text="$1" token chat
  if [ "$MODE" = "dry" ]; then
    log "(dry-run) würde Telegram senden: $text"
    return 0
  fi
  token="$(env_value TELEGRAM_BOT_TOKEN)"
  chat="$(env_value ADMIN_TELEGRAM_CHAT_ID)"
  if [ -z "$token" ] || [ -z "$chat" ]; then
    log "Telegram nicht konfiguriert (TELEGRAM_BOT_TOKEN/ADMIN_TELEGRAM_CHAT_ID leer)"
    return 1
  fi
  if curl -fsS -m 20 -o /dev/null \
      "https://api.telegram.org/bot${token}/sendMessage" \
      --data-urlencode "chat_id=${chat}" \
      --data-urlencode "text=${text}"; then
    log "Telegram gesendet: $text"
  else
    log "Telegram-Versand fehlgeschlagen: $text"
    return 1
  fi
}

healthy() {
  curl -fs -m 20 -o /dev/null "$HEALTH_URL"
}

# Zustand: fail_since, restarts, last_restart, notified (alles Unix-Sekunden/Zahlen)
fail_since=0; restarts=0; last_restart=0; notified=0
load_state() {
  [ -f "$STATE_FILE" ] || return 0
  while IFS='=' read -r k v; do
    case "$k" in
      fail_since|restarts|last_restart|notified)
        [[ "$v" =~ ^[0-9]+$ ]] && printf -v "$k" '%s' "$v" ;;
    esac
  done < "$STATE_FILE"
}
save_state() {
  [ "$MODE" = "dry" ] && return 0
  printf 'fail_since=%s\nrestarts=%s\nlast_restart=%s\nnotified=%s\n' \
    "$fail_since" "$restarts" "$last_restart" "$notified" > "$STATE_FILE"
}

trim_log() {
  [ -f "$LOG_FILE" ] || return 0
  if [ "$(wc -l < "$LOG_FILE")" -gt 2000 ]; then
    tail -n 1000 "$LOG_FILE" > "$LOG_FILE.tmp" && mv "$LOG_FILE.tmp" "$LOG_FILE"
  fi
}

fmt_minutes() { echo $(( ($1 + 59) / 60 )); }

if [ "$MODE" = "test" ]; then
  telegram "🧪 SensIR-Wächter auf der NAS: Testnachricht. Telegram-Meldungen funktionieren."
  exit $?
fi

exec 9> "$LOCK_FILE"
if ! flock -n 9; then
  log "Vorheriger Lauf läuft noch - übersprungen"
  exit 0
fi

trim_log
load_state
now=$(date +%s)

if healthy; then
  [ "$MODE" = "dry" ] && log "SensIR antwortet (gesund)"
  if [ "$fail_since" -gt 0 ]; then
    down=$(( now - fail_since ))
    log "SensIR antwortet wieder (gestört seit $(fmt_minutes $down) Min, Neustarts: $restarts)"
    if [ "$notified" -gt 0 ]; then
      telegram "✅ SensIR antwortet wieder (nach etwa $(fmt_minutes $down) Minuten Störung)."
    fi
  fi
  fail_since=0; restarts=0; last_restart=0; notified=0
  save_state
  exit 0
fi

# --- SensIR antwortet nicht ---
if [ "$fail_since" -eq 0 ]; then
  fail_since=$now
  log "SensIR antwortet nicht - Beobachtung beginnt"
  save_state
  exit 0
fi

down=$(( now - fail_since ))
log "SensIR antwortet seit $(fmt_minutes $down) Min nicht"

if [ "$down" -lt $(( FAIL_MINUTES * 60 )) ]; then
  save_state
  exit 0
fi

if [ "$restarts" -ge "$MAX_RESTARTS" ]; then
  if [ "$notified" -lt 2 ]; then
    telegram "🛑 SensIR ist seit etwa $(fmt_minutes $down) Minuten nicht erreichbar. $restarts automatische Neustarts des Container Managers haben nicht geholfen - bitte von Hand prüfen. Der Wächter versucht es nicht weiter, bis SensIR wieder antwortet."
    notified=2
  fi
  save_state
  exit 0
fi

if [ "$last_restart" -gt 0 ] && [ $(( now - last_restart )) -lt $(( COOLDOWN_MINUTES * 60 )) ]; then
  log "Neustart-Pause aktiv (letzter Neustart vor $(fmt_minutes $(( now - last_restart ))) Min)"
  save_state
  exit 0
fi

attempt=$(( restarts + 1 ))
telegram "⚠️ SensIR ist seit etwa $(fmt_minutes $down) Minuten nicht erreichbar. Der Wächter startet jetzt den Container Manager neu (Versuch $attempt von $MAX_RESTARTS)."
[ "$notified" -lt 1 ] && notified=1

if [ "$MODE" = "dry" ]; then
  log "(dry-run) würde jetzt '$SYNOPKG restart ContainerManager' ausführen"
  exit 0
fi

restarts=$attempt
last_restart=$now
save_state

log "Starte Container Manager neu (Versuch $attempt)"
restart_out="$(timeout 600 "$SYNOPKG" restart ContainerManager 2>&1)"
restart_rc=$?
log "synopkg restart: rc=$restart_rc $(echo "$restart_out" | tail -n 1)"

waited=0
while [ "$waited" -lt "$WAIT_AFTER_RESTART" ]; do
  sleep 15
  waited=$(( waited + 15 ))
  if healthy; then
    total=$(( $(date +%s) - fail_since ))
    log "Neustart erfolgreich: SensIR antwortet nach ${waited}s wieder"
    telegram "✅ Neustart erfolgreich: SensIR läuft wieder (Störung insgesamt etwa $(fmt_minutes $total) Minuten). Bewegungsmeldungen aus dieser Zeit fehlen."
    fail_since=0; restarts=0; last_restart=0; notified=0
    save_state
    exit 0
  fi
done

if [ "$restarts" -ge "$MAX_RESTARTS" ]; then
  next="Weitere automatische Versuche gibt es nicht - bitte von Hand prüfen."
  notified=2
else
  next="Nächster automatischer Versuch frühestens in $COOLDOWN_MINUTES Minuten."
fi
log "Neustart erfolglos: SensIR antwortet $(( WAIT_AFTER_RESTART / 60 )) Min nach dem Neustart nicht (synopkg rc=$restart_rc)"
telegram "❌ Neustart erfolglos: SensIR antwortet auch $(( WAIT_AFTER_RESTART / 60 )) Minuten nach dem Neustart des Container Managers nicht. $next"
save_state
exit 1
