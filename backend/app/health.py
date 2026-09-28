"""Prozess-Heartbeat für Docker-Healthcheck/automatischen Neustart.

Hintergrund: am 2026-09-28 live beobachtet, dass der Backend-Prozess nach
einem DNS-Ausfall (Tuya-API-Auflösung schlug fehl, siehe
docs/tuya-camera-motion-troubleshooting.md) offenbar komplett einfror -
kein Absturz, kein Crash, aber alle Hintergrund-Threads (Shelly-Polling,
APScheduler, Tuya-Pulsar) blieben über zwei Stunden lang unbemerkt stehen,
während der HTTP-Server selbst weiter ganz normal auf Anfragen antwortete
(`docker ps` zeigte "running", `GET /` lieferte 200 OK). Ein reiner
HTTP-Healthcheck hätte das also NICHT erkannt.

Deshalb: ein leichter, unabhängiger APScheduler-Job (`_heartbeat_job` in
scheduler.py) aktualisiert minütlich `touch()`. `/healthz` meldet 503,
sobald der letzte Heartbeat zu alt ist - das erkennt genau das oben
beschriebene Einfrieren, weil ein hängender Scheduler-Thread auch keine
Heartbeats mehr schreibt. Der `autoheal`-Sidecar-Container (siehe
docker-compose.yml) beobachtet den Docker-HEALTHCHECK-Status und startet
den Container automatisch neu, sobald er "unhealthy" wird.
"""

import time

_last_heartbeat = time.time()

# Heartbeat-Job läuft jede Minute (scheduler.py) - großzügiger Puffer, damit
# ein einzelner verzögerter Tick (z. B. kurzzeitig hohe Last) nicht sofort
# einen Neustart auslöst.
MAX_AGE_SECONDS = 300


def touch() -> None:
    global _last_heartbeat
    _last_heartbeat = time.time()


def seconds_since_heartbeat() -> float:
    return time.time() - _last_heartbeat


def is_healthy() -> bool:
    return seconds_since_heartbeat() < MAX_AGE_SECONDS
