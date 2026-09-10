# SensIR

"Babyphone für Senioren ohne Bild und Ton" — Sensoren melden Alltags­aktivität
einer älteren Person. Bleibt die erwartete Aktivität in einem Zeitfenster aus,
werden Angehörige per Telegram alarmiert. Ein KI-Modell lernt pro Haushalt den
normalen Tagesablauf und ersetzt nach und nach die manuell konfigurierten
Zeitfenster.

**Ein Backend, drei austauschbare Sensor-Quellen:**

| Quelle | Geräte | Weg |
|---|---|---|
| `ir_bridge` | Pearl-IR-Empfänger mit Tasmota ("YTF IR Bridge") neben dem Fernseher | MQTT im LAN / über Broker |
| `tuya` | Tuya-/SmartLife-Geräte (Bewegungsmelder, Tür­kontakte, Rauchmelder, Steckdosen mit Verbrauchsmessung) | Tuya Cloud API + Pulsar-Stream |
| `shelly` | Shelly-Geräte (Motion, Plug S/PM, i4, Smoke) | Shelly Cloud API + Cloud-WebSocket |

Alle Quellen schreiben dasselbe kanonische `SensorEvent`. ML, Alerting und
Status arbeiten quellenunabhängig — ein Haushalt kann Geräte mehrerer Marken
gemischt haben.

Hintergrund, Personas, Business Model Canvas etc. siehe die Projekt-Doku
(nicht Teil dieses Repos).

## Architektur

```
 IR-Bridge (Tasmota)     Tuya-/SmartLife-Geräte     Shelly-Geräte
        │ MQTT                  │ Tuya Cloud              │ Shelly Cloud
        ▼                       ▼                         ▼
   Mosquitto            openapi.tuya*.com          shelly-*.shelly.cloud
        │                  + Pulsar-Stream            + Cloud-WebSocket
        └───────────────┬───────┴─────────────────────────┘
                        ▼
   FastAPI Backend
   ├─ ingest/registry.py  → startet die aktiven Quellen (ENV-Flags)
   │   ├─ mqtt_ir.py       → SensorEvent(kind="ir")
   │   ├─ tuya.py          → Polling + Pulsar → normalize → SensorEvent
   │   └─ shelly.py        → Polling + WebSocket → normalize → SensorEvent
   ├─ scheduler.py         → APScheduler: periodische Prüfung + nächtliches ML-Training
   ├─ alerting/engine.py   → Zeitfenster-Check + sofortige Rauch-/Gas-Alarme
   ├─ ml/window_model.py   → KernelDensity je Haushalt (über alle SensorEvents)
   ├─ alerting/telegram.py → Alarme an konfigurierte Kontakte
   ├─ api/                 → REST (Haushalte, Sensoren, Quellen-Discovery, Kontakte, Zeitfenster, Status)
   └─ web/                 → Dashboard (Ampel je Haushalt)
        │
        ▼
   Postgres (Events, Konfiguration, Historie)
```

## Voraussetzungen

- Docker + Docker Compose
- Ein oder mehrere Pearl-IR-Fernbedienungen mit Tasmota-Firmware
  (Modul-Typ "YTF IR Bridge (62)"), siehe `Pearl-IR-Sender-Tasmota.pdf` für
  die Flash-Anleitung.

## Setup

```bash
cp .env.example .env
# .env anpassen: Postgres-Passwort, MQTT-Zugangsdaten, Telegram-Bot-Token

./scripts/create_mqtt_user.sh   # legt mosquitto/config/passwd an

docker compose up --build
```

Die erste Migration wird beim Start automatisch ausgeführt
(`alembic upgrade head` im Backend-Container). Dashboard danach unter
`http://localhost:8000/`, API-Dokumentation unter `http://localhost:8000/docs`.

## Einen Sensor anlernen

1. Tasmota-Gerät wie in `Pearl-IR-Sender-Tasmota.pdf` beschrieben flashen
   und einrichten. Modul-Typ "YTF IR Bridge (62)", MQTT-Server/Port/Zugangsdaten
   auf den Mosquitto-Broker aus `.env` zeigen lassen. Der Tasmota-Topic-Name
   (Configuration → MQTT → Topic) ist der `mqtt_topic` unten.
2. Sensor + Haushalt in SensIR anlegen:

   ```bash
   curl -X POST localhost:8000/api/households -d '{"name": "Hannelore Meyer"}' -H 'Content-Type: application/json'
   # IR-Bridge:
   curl -X POST localhost:8000/api/sensors -d '{"household_id":1,"name":"Wohnzimmer","kind":"ir_bridge","mqtt_topic":"sensir-01"}' -H 'Content-Type: application/json'
   ```
3. Kontakte und (optional, siehe unten) Zeitfenster über das Dashboard
   unter `/households/1` anlegen.

## Tuya-/SmartLife-Geräte anbinden

1. Auf [iot.tuya.com](https://iot.tuya.com) ein Konto + **Cloud-Projekt** anlegen
   (Data Center = Region der SmartLife-App, EU meist `Central Europe`).
   APIs *IoT Core*, *Authorization*, *Device Status Notification* abonnieren.
2. **Devices → Link App Account** → QR-Code mit der SmartLife-App der Person
   scannen. Die *UID* dort ist `TUYA_APP_ACCOUNT_UID`.
3. In `.env`: `TUYA_ENABLED=true`, `TUYA_ACCESS_ID/SECRET`, `TUYA_REGION`,
   `TUYA_APP_ACCOUNT_UID`. Neustart.
4. Geräte auflisten und als Sensor anlegen:

   ```bash
   curl localhost:8000/api/sources/tuya/devices
   curl -X POST localhost:8000/api/sensors -H 'Content-Type: application/json' \
     -d '{"household_id":1,"name":"Flur Bewegung","kind":"tuya","external_id":"<device-id>","config":{"room":"flur"}}'
   # Steckdose mit Verbrauchsmessung: "config":{"room":"kueche","on_threshold_w":15}
   ```

## Shelly-Geräte anbinden

1. Shelly-App/Cloud → **Einstellungen → Autorisierungs-Cloud-Key**. Dort stehen
   der Key und der Server-Host (z. B. `https://shelly-59-eu.shelly.cloud`).
2. In `.env`: `SHELLY_ENABLED=true`, `SHELLY_AUTH_KEY`, `SHELLY_API_HOST`.
   Neustart.
3. `curl localhost:8000/api/sources/shelly/devices`, dann Sensor mit
   `"kind":"shelly","external_id":"<device-id>"` anlegen.

Rauch-/Gasmelder (Tuya oder Shelly) lösen unabhängig vom Zeitfenster sofort
einen Telegram-Alarm an alle Kontakte aus.

## Zeitfenster: manuell vs. KI

Ohne konfiguriertes Zeitfenster gilt der Default aus `.env`
(`DEFAULT_WINDOW_START/END/MIN_ACTIONS`). Sobald ein Haushalt
`ML_MIN_SAMPLES` (Default 200) IR-Ereignisse gesammelt hat, übernimmt beim
nächsten nächtlichen Training (`ML_TRAIN_HOUR_UTC`) automatisch das
gelernte Modell aus `app/ml/window_model.py` die Erwartungswert-Berechnung —
die manuell gepflegten `ObservationWindow`-Einträge dienen dann nur noch
als Fallback für neue/verwaiste Haushalte.

## Telegram-Bot

Bot bei [@BotFather](https://t.me/BotFather) anlegen, Token in
`TELEGRAM_BOT_TOKEN` eintragen. Die `chat_id` eines Kontakts bekommt man,
indem die Person dem Bot einmal schreibt und man die ID über
`https://api.telegram.org/bot<TOKEN>/getUpdates` ausliest — ein
Onboarding-Flow in der App fehlt hier noch bewusst (PoC-Stand).

## Bekannte Lücken / nächste Schritte

- **Sensor-Sicherheit**: aktuell MQTT-Benutzername/Passwort ohne TLS
  (PoC-Entscheidung). Für den produktiven Rollout: MQTTS mit
  Client-Zertifikat pro Sensor (Tasmota unterstützt das nativ), da natives
  WireGuard auf dem ESP8266 nicht praktikabel ist.
- **ML-Modell**: keine Mitternachts-Wraparound-Behandlung im
  KernelDensity-Fenster — für die Zielgruppe (abendliches Fernsehen) bisher
  unkritisch, siehe Docstring in `app/ml/window_model.py`.
- **Contact-Onboarding**: `telegram_chat_id` wird aktuell manuell ermittelt
  und eingetragen, kein Self-Service-Flow.
- Aus der Projekt-Doku vorgemerkte Erweiterungen: Remote-Fernbedienung des
  Senioren-TVs, Temperatur-/Feuchtigkeitssensor (Schimmelwarnung),
  Alzheimer-Früherkennung durch Langzeit-Beobachtung.

## Entwicklung ohne Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# .env im Projekt-Root muss auf lokal erreichbare Postgres/Mosquitto-Instanzen zeigen
alembic upgrade head
uvicorn app.main:app --reload
```
