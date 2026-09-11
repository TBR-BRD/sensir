# SensIR — Gesamtübersicht

Stand: 2026-09-10 (Commit `63630e6`). Diese Datei fasst alles zusammen, was
über README + CHANGELOG verteilt ist: Konzept, Architektur, Datenmodell, alle
drei Sensor-Quellen mit Setup, API-Referenz, Deployment, offene Punkte.

Repo: `github.com/TBR-BRD/sensir` (privat, Branch `main`). Lokal: `~/sensir`.

> **Hinweis:** `sensir-smartlife` und `sensir-shelly` sind ältere, separate
> Repos zum selben Thema — **obsolet**, seit 2026-09-10 läuft alles hier in
> einem Backend zusammen. Können archiviert/gelöscht werden.

---

## 1. Idee

"Babyphone für Senioren ohne Bild und Ton": Sensoren im Zuhause einer älteren
Person melden Alltagsaktivität (Fernbedienung benutzt, Bewegung, Tür/Fenster,
Gerät ein-/ausgeschaltet …). Bleibt die für eine Tageszeit erwartete Aktivität
aus, werden Angehörige per **Telegram** alarmiert. Ein **KI-Modell pro
Haushalt** lernt mit der Zeit den normalen Tagesablauf und ersetzt so
nach und nach fest konfigurierte Zeitfenster.

Rauch-/Gasmelder lösen **unabhängig vom Zeitfenster sofort** einen Alarm aus.

## 2. Architektur

```
 IR-Bridge (Tasmota)     Tuya-/SmartLife-Geräte     Shelly-Geräte
        │ MQTT                  │ Tuya Cloud              │ Shelly Cloud
        ▼                       ▼                         ▼
   Mosquitto            openapi.tuya*.com          shelly-*.shelly.cloud
        │                  + Pulsar-Stream            + Cloud-WebSocket
        └───────────────┬───────┴─────────────────────────┘
                        ▼
   FastAPI-Backend
   ├─ ingest/registry.py  → startet die per .env aktiven Quellen
   │   ├─ mqtt_ir.py       → SensorEvent(kind="ir")
   │   ├─ tuya.py          → Polling + Pulsar → normalize → SensorEvent
   │   └─ shelly.py        → Polling + WebSocket → normalize → SensorEvent
   ├─ scheduler.py         → APScheduler: periodische Prüfung + nächtliches ML-Training
   ├─ alerting/engine.py   → Zeitfenster-Check + sofortige Rauch-/Gas-Alarme
   ├─ ml/window_model.py   → KernelDensity je Haushalt (über alle SensorEvents)
   ├─ alerting/telegram.py → sendet Alarme an konfigurierte Kontakte
   ├─ api/                 → REST (Haushalte, Sensoren, Quellen-Discovery, Kontakte, Zeitfenster, Status)
   └─ web/                 → Dashboard (Ampel je Haushalt)
        │
        ▼
   Postgres (Events, Konfiguration, Historie)
```

**Kernprinzip:** alle drei Quellen schreiben dasselbe kanonische `SensorEvent`.
ML, Alerting und Status-Berechnung sind **quellenunabhängig** — ein Haushalt
kann Geräte aller drei Marken gemischt haben, das Modell sieht sie als eine
gemeinsame Aktivitäts-Zeitreihe.

### Warum dieser Ansatz

- **Multi-Household von Anfang an**: kein Hobby-Skript für eine Person, sondern
  ein Datenmodell, das mehrere beobachtete Haushalte mit je eigenen Sensoren,
  Kontakten und Zeitfenstern trägt.
- **KernelDensity statt Blackbox-ML**: ein 1D-Gaussian-KDE über die
  Tageszeit (Minute des Tages) aller Ereignisse eines Haushalts ist erklärbar,
  braucht wenig Trainingsdaten (`ML_MIN_SAMPLES`, Standard 200 Ereignisse) und
  läuft ohne GPU auf der Synology.
- **Cloud statt lokalem Hub bei den Eltern**: Tuya- und Shelly-Geräte bleiben
  in ihrer jeweiligen App/Cloud, das Backend zieht sich die Daten von dort —
  keine Netzwerk-/VPN-Einrichtung im Elternhaus nötig.

## 3. Datenmodell (`backend/app/models.py`)

| Tabelle | Zweck |
|---|---|
| `households` | ein beobachteter Haushalt (Name, Zeitzone) |
| `sensors` | ein Gerät, einer Quelle zugeordnet (`kind`) |
| `sensor_events` | kanonisches Aktivitätsereignis (ex-`IrEvent`) |
| `observation_windows` | Zeitfenster mit Mindest-Aktionszahl, manuell oder `source=ml` |
| `activity_checks` | Ergebnis der Auswertung eines Zeitfensters für einen Tag |
| `contacts` | Angehörige mit `telegram_chat_id` |
| `alert_log` | jede gesendete Alarm-Nachricht |

### `Sensor`

| Feld | Bedeutung |
|---|---|
| `kind` | `ir_bridge` \| `tuya` \| `shelly` — bestimmt die Ingest-Quelle |
| `mqtt_topic` | nur `ir_bridge`: Tasmota-Basistopic, z. B. `sensir-01` |
| `external_id` | nur `tuya`/`shelly`: Cloud-Geräte-ID |
| `config` (JSON) | quellenspezifische Feinjustierung, z. B. `{"room": "flur", "on_threshold_w": 15}` |
| `last_seen_at` | Zeitpunkt des letzten empfangenen Ereignisses |

Eindeutigkeit: `(kind, external_id)` ist unique (`uq_sensor_kind_external`),
`mqtt_topic` weiterhin einzeln unique.

### `SensorEvent` (ex-`IrEvent`)

| Feld | Bedeutung |
|---|---|
| `kind` | `ir` \| `motion` \| `door` \| `window` \| `button` \| `light` \| `appliance_on` \| `appliance_off` \| `presence` \| `smoke` \| `gas` |
| `value` | z. B. Watt bei Steckdosen |
| `safety` | `True` bei `smoke`/`gas` — löst sofortigen Alarm aus, zählt nicht für ML/Zeitfenster |
| `protocol` / `bits` / `data_hex` | nur bei `kind="ir"`, Tasmota-`IrReceived`-Felder |
| `raw_payload` | Original-Payload der Quelle, zur Fehlersuche |

Migration: `backend/alembic/versions/0002_multi_source.py` (benennt
`ir_events` → `sensor_events` um, ergänzt die neuen Spalten). Läuft beim
Container-Start automatisch (`alembic upgrade head`).

## 4. Ingest-Quellen (`backend/app/ingest/`)

`registry.py` liest beim Start die `*_ENABLED`-Flags aus `.env` und startet nur
die aktivierten Quellen. Jede Quelle läuft in ihrem eigenen Hintergrund-Thread
(`app/ingest/base.py`: `Source`/`PollingSource`).

### 4.1 IR-Bridge über MQTT (`mqtt_ir.py`)

- Hardware: Pearl-IR-Empfänger mit **Tasmota**-Firmware, Modul-Typ
  „YTF IR Bridge (62)“, neben dem Fernseher.
- Tasmota publiziert empfangene Fernbedienungssignale auf
  `tele/<topic>/RESULT` als `IrReceived`-JSON.
- `paho-mqtt` läuft in einem eigenen Netzwerk-Thread (`loop_start`), getrennt
  vom asyncio-Loop von FastAPI — bewusste Entscheidung, da jede Nachricht
  einen synchronen DB-Write auslöst und die Nachrichtenrate gering ist.
- Matching: `Sensor.mqtt_topic == <topic-Basisname>` und `kind=ir_bridge`.
- **Setup:**
  1. Tasmota flashen/konfigurieren, MQTT-Server auf den Mosquitto-Broker
     zeigen lassen (Zugangsdaten aus `.env`).
  2. Sensor anlegen:
     ```bash
     curl -X POST localhost:8000/api/sensors -H 'Content-Type: application/json' \
       -d '{"household_id":1,"name":"Wohnzimmer","kind":"ir_bridge","mqtt_topic":"sensir-01"}'
     ```
- Konfiguration: `MQTT_ENABLED`, `MQTT_HOST/PORT/USERNAME/PASSWORD`,
  `MQTT_RESULT_TOPIC_FILTERS` (Standard `tele/+/RESULT,stat/+/RESULT`).
- Mosquitto-Broker läuft als eigener Compose-Service, wird **nur für diese
  Quelle** gebraucht (bei reinem Tuya/Shelly-Betrieb `MQTT_ENABLED=false`
  setzen und den Service optional weglassen).

### 4.2 Tuya / SmartLife Cloud (`tuya.py`)

- Nutzt `tuya-connector-python` (`TuyaOpenAPI` für REST, `TuyaOpenPulsar` für
  Echtzeit-Events).
- **Zwei Datenwege gleichzeitig:**
  - **Polling** (`PollingSource`, Intervall `SOURCE_POLL_INTERVAL_SECONDS`,
    Standard 120 s): fragt für jeden `kind=tuya`-Sensor
    `GET /v1.0/devices/{id}/status` ab.
  - **Pulsar-Stream** (`TUYA_PULSAR_ENABLED=true`): abonniert Tuyas
    Echtzeit-Nachrichtenqueue, liefert Statusänderungen praktisch sofort.
    Läuft als eigener Thread, unabhängig vom Polling.
- Beide Wege laufen durch `normalize_tuya()` (`app/ingest/normalize.py`):
  mappt Tuya-Statuscodes → kanonisches `kind` (siehe Tabelle unten),
  Steckdosen-Watt-Werte werden über `on_threshold_w` (aus `Sensor.config`,
  Standard 10 W) zu `appliance_on`/`appliance_off`-**Flanken** (nur beim
  Wechsel, nicht bei jedem Poll).
- **Setup (siehe auch README, Abschnitt "Tuya-/SmartLife-Geräte anbinden"):**
  1. Auf [iot.tuya.com](https://iot.tuya.com) Konto + Cloud-Projekt anlegen,
     Data Center = Region der SmartLife-App. APIs *IoT Core*, *Authorization*,
     *Device Status Notification* abonnieren.
  2. **Devices → Link App Account**, QR-Code mit der SmartLife-/Tuya-App der
     beobachteten Person scannen. Die dort angezeigte UID = `TUYA_APP_ACCOUNT_UID`.
  3. `.env`: `TUYA_ENABLED=true`, `TUYA_ACCESS_ID`, `TUYA_ACCESS_SECRET`,
     `TUYA_REGION` (`eu`/`us`/`cn`/`in`), `TUYA_APP_ACCOUNT_UID`.
  4. Geräte auflisten und Sensor anlegen:
     ```bash
     curl localhost:8000/api/sources/tuya/devices
     curl -X POST localhost:8000/api/sensors -H 'Content-Type: application/json' \
       -d '{"household_id":1,"name":"Flur Bewegung","kind":"tuya","external_id":"<device-id>","config":{"room":"flur"}}'
     # Steckdose mit Verbrauchsmessung:
     #   "config":{"room":"kueche","on_threshold_w":15}
     ```
- **Bekannte Falle:** das Tuya-Trial-Kontingent für *IoT Core* läuft nach ca.
  einem Monat ab und muss im Tuya-Dashboard kostenlos verlängert werden —
  sonst antworten die APIs mit `code 28841105`.
- **Noch offen:** Pulsar-Nachrichtenformat wurde nur gegen die pychromecast-
  ähnliche Doku entworfen, nicht gegen echte Tuya-Geräte verifiziert.

### 4.3 Shelly Cloud (`shelly.py`)

- **Zwei Datenwege:**
  - **Polling**: `POST {SHELLY_API_HOST}/device/status` mit `id` + `auth_key`
    je `kind=shelly`-Sensor.
  - **Cloud-WebSocket** (`SHELLY_WS_ENABLED=true`, braucht das Paket
    `websocket-client`): verbindet sich zu
    `wss://.../shelly/wss/hk/events?t=<auth_key>`, verarbeitet Push-Events.
    Reconnect mit 15 s Backoff bei Verbindungsabbruch.
- `normalize_shelly()` erkennt sowohl **Gen2/Plus/Pro**-RPC-Komponenten
  (`sensor:0`, `smoke:0`, `input:0`, `switch:0`, `pm1:0`, `em1:0`) als auch das
  flachere **Gen1**-Format (`sensor.motion`, `meters[0].power`, `inputs[]`).
- **Setup (siehe auch README, Abschnitt "Shelly-Geräte anbinden"):**
  1. Shelly-App/Cloud → **Einstellungen → Autorisierungs-Cloud-Key**. Dort
     stehen Key **und** Server-Host (z. B. `https://shelly-59-eu.shelly.cloud`
     — nicht der generische `shelly-eu`-Host, sondern der kontospezifische!).
  2. `.env`: `SHELLY_ENABLED=true`, `SHELLY_AUTH_KEY`, `SHELLY_API_HOST`.
  3. Geräte auflisten und Sensor anlegen:
     ```bash
     curl localhost:8000/api/sources/shelly/devices
     curl -X POST localhost:8000/api/sensors -H 'Content-Type: application/json' \
       -d '{"household_id":1,"name":"Küche Steckdose","kind":"shelly","external_id":"<device-id>","config":{"room":"kueche","on_threshold_w":15}}'
     ```
- **Noch offen:** WebSocket-Nachrichtenformat (`_on_ws_message`) ist nach
  bester Doku-Lage implementiert, aber nicht gegen ein echtes Shelly-Cloud-Konto
  getestet — beim ersten Live-Test `docker compose logs -f backend` beobachten.

### 4.4 Normalisierung — Statuscode-Mapping (`normalize.py`)

| Kanonisches `kind` | Tuya-Codes | Shelly |
|---|---|---|
| `motion` | `pir`, `presence_state`, `presence`, `occupancy` | `sensor:N.motion` (Gen2), `sensor.motion` (Gen1) |
| `door` | `doorcontact_state`, `door_state`, `contact_state` | — |
| `window` | `window_state`, `windowcontact_state` | — |
| `button` | `switch1_value`, `switch_value`, `click` | `input:N.state` (Gen2), `inputs[].input==1` (Gen1) |
| `light` | `switch_led`, `switch`, `switch_1`, `led_switch` | — |
| `appliance_on`/`appliance_off` | `cur_power`, `power` (÷10, Tuya liefert 0,1 W) | `switch:N.apower`, `pm1:N`/`em1:N`, `meters[0].power` (Gen1) |
| `smoke` (`safety=True`) | `smoke_sensor_state`, `smoke_sensor_status`, `smoke` | `smoke:N.alarm` |
| `gas` (`safety=True`) | `gas_sensor_state`, `gas_sensor_status`, `ch4_sensor_state`, `co_state` | — (noch nicht gemappt) |

Steckdosen-Ereignisse sind **Flanken**: `appliance_on`/`appliance_off` wird nur
beim Wechsel über/unter `on_threshold_w` erzeugt, nicht bei jedem
Polling-Zyklus mit gleichbleibendem Wert (`_plug_edges()`, Zustand pro
Sensor-Key im Prozess-Speicher der jeweiligen Source-Instanz gehalten).

`ACTIVITY_KINDS` (alles außer `smoke`/`gas`) zählt für Zeitfenster- und
ML-Auswertung als "Person war aktiv". `SAFETY_KINDS = {smoke, gas}` wird davon
ausgeschlossen und stattdessen sofort alarmiert.

## 5. Auswertung: Zeitfenster, ML, Alerting

### 5.1 Beobachtungsfenster (`ObservationWindow` / `alerting/engine.py`)

- Pro Haushalt können Zeitfenster konfiguriert werden: Wochentag (oder `null`
  = jeden Tag), Start-/Endzeit, `min_actions`.
- Ohne konfiguriertes Fenster gilt der Default aus `.env`
  (`DEFAULT_WINDOW_START/END/MIN_ACTIONS`, Standard 18:00–24:00, 2 Aktionen).
- `scheduler.py` lässt `run_periodic_check()` alle `CHECK_INTERVAL_MINUTES`
  (Standard 15) laufen: für jedes heute aktive Fenster wird die Zahl der
  `SensorEvent`s (ohne `safety`) im Fenster gezählt.
  - genug Aktionen **schon vor Fensterende** → `ActivityCheck` `positive`,
    fertig.
  - Fenster vorbei, zu wenig Aktionen → `ActivityCheck` `negative` →
    **Telegram-Alarm an alle aktiven Kontakte** des Haushalts.
- **Sofort-Alarme:** `_check_safety()` läuft im selben Takt, sucht
  `SensorEvent`s mit `safety=True` der letzten `CHECK_INTERVAL_MINUTES + 5`
  Minuten und alarmiert **unabhängig vom Zeitfenster**, entprellt über
  `AlertLog` (ein Marker pro Sensor+Minute, kein Doppel-Alarm).

### 5.2 ML-Modell (`ml/window_model.py`)

- Einmal täglich (`ML_TRAIN_HOUR_UTC`, Standard 3 Uhr UTC) trainiert
  `train_all_household_models()` pro Haushalt ein **1D-Gaussian-KernelDensity**
  über die Minute-des-Tages aller (Nicht-Safety-)`SensorEvent`s.
- Erst ab `ML_MIN_SAMPLES` (Standard 200) Ereignissen wird trainiert; vorher
  bleibt die feste `ObservationWindow`-Konfiguration maßgeblich.
- `expected_events(household_id, start_minute, end_minute)` gibt die für ein
  Zeitintervall erwartete Ereigniszahl zurück (Dichte × Gesamtereignisse ÷
  Trainingstage). `_sufficient()` in `engine.py` nutzt das statt
  `min_actions`, sobald ein Modell existiert: tatsächliche Zahl muss
  ≥ `ANOMALY_RATIO` (0,3) × erwartete Zahl sein.
- Modelle liegen als `.joblib` unter `ML_MODEL_DIR` (Compose-Volume
  `ml_models`), pro Haushalt eine Datei.
- **Bekannte Lücke** (laut Docstring): keine Mitternachts-Wraparound-Behandlung
  — für die Zielgruppe (abendliche Fernbedienungsnutzung) bisher unkritisch.

### 5.3 Telegram (`alerting/telegram.py`)

- `send_telegram_message(chat_id, text)` — einfacher `POST` an die Bot-API.
- Bot bei [@BotFather](https://t.me/BotFather) anlegen, Token in
  `TELEGRAM_BOT_TOKEN`.
- `Contact.telegram_chat_id` wird aktuell **manuell** ermittelt: Person
  schreibt dem Bot einmal, `chat_id` über
  `https://api.telegram.org/bot<TOKEN>/getUpdates` auslesen. Kein
  Self-Service-Onboarding (bewusste PoC-Lücke, siehe README).

## 6. API-Referenz (`/api`, siehe auch `/docs` für Swagger)

| Endpunkt | Methode | Zweck |
|---|---|---|
| `/api/households` | GET/POST | Haushalte |
| `/api/sensors` | GET/POST | Sensoren; POST braucht `kind` + (`mqtt_topic` **oder** `external_id`) |
| `/api/sensors/{id}` | DELETE | Sensor löschen |
| `/api/sources/tuya/devices` | GET | Tuya-Cloud-Geräte des verknüpften Kontos |
| `/api/sources/shelly/devices` | GET | Shelly-Cloud-Geräte des Kontos |
| `/api/households/{id}/contacts` | GET/POST | Kontakte |
| `/api/households/{id}/windows` | GET/POST | Beobachtungsfenster |
| `/api/households/{id}/status` | GET | aktueller Status (letztes Ereignis, Tagesereignisse, aktives Fenster, letzter Check) |

Dashboard (kein API, HTML): `/` (Ampel-Übersicht), `/households/{id}` (Detail).

## 7. Konfiguration (`.env`, siehe `.env.example`)

```dotenv
# Postgres
POSTGRES_USER=sensir
POSTGRES_PASSWORD=change-me
POSTGRES_DB=sensir
DATABASE_URL=postgresql+psycopg2://sensir:change-me@postgres:5432/sensir

# Quelle 1: IR-Bridge über MQTT
MQTT_ENABLED=true
MQTT_HOST=mosquitto
MQTT_PORT=1883
MQTT_USERNAME=sensir
MQTT_PASSWORD=change-me
MQTT_RESULT_TOPIC_FILTERS=tele/+/RESULT,stat/+/RESULT

# Quelle 2: Tuya/SmartLife
TUYA_ENABLED=false
TUYA_ACCESS_ID=
TUYA_ACCESS_SECRET=
TUYA_REGION=eu
TUYA_APP_ACCOUNT_UID=
TUYA_PULSAR_ENABLED=true

# Quelle 3: Shelly
SHELLY_ENABLED=false
SHELLY_AUTH_KEY=
SHELLY_API_HOST=https://shelly-eu.shelly.cloud
SHELLY_WS_ENABLED=true

SOURCE_POLL_INTERVAL_SECONDS=120   # Tuya + Shelly Polling-Fallback

# Alerting
TELEGRAM_BOT_TOKEN=
CHECK_INTERVAL_MINUTES=15
DEFAULT_WINDOW_START=18:00
DEFAULT_WINDOW_END=24:00
DEFAULT_MIN_ACTIONS=2
ML_MIN_SAMPLES=200
ML_TRAIN_HOUR_UTC=3
```

## 8. Deployment

Zielsystem: **Synology DS720+** über Docker/Container Manager.

```bash
cp .env.example .env        # ausfüllen
./scripts/create_mqtt_user.sh   # nur nötig, wenn MQTT_ENABLED=true
docker compose up --build
```

- `docker-compose.yml`: `postgres` + `mosquitto` (optional bei reinem
  Tuya/Shelly-Betrieb) + `backend`.
- Migrationen laufen beim Start automatisch (`alembic upgrade head` im
  Backend-Container-Command).
- Dashboard: `http://<synology>:8000/`, API-Doku: `http://<synology>:8000/docs`.
- Persistenz: `pgdata`-Volume (Postgres), `ml_models`-Volume
  (`app/ml/models/*.joblib`).

### Entwicklung ohne Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

## 9. Sicherheit / bekannte Lücken

- **MQTT**: aktuell Benutzername/Passwort ohne TLS (PoC-Entscheidung). Für den
  produktiven Rollout: MQTTS mit Client-Zertifikat pro Sensor (Tasmota
  unterstützt das nativ) — natives WireGuard auf dem ESP8266 ist nicht
  praktikabel.
- **Tuya/Shelly-Zugangsdaten** liegen nur in `.env` (nicht im Git,
  `.gitignore` deckt das ab).
- **Contact-Onboarding**: `telegram_chat_id` manuell ermittelt, kein
  Self-Service-Flow.
- **ML-Wraparound**: siehe 5.2.
- **Tuya-Trial**: monatliche Verlängerung im Tuya-Dashboard nötig.
- **Pulsar-/Shelly-WS-Format**: noch nicht gegen echte Geräte verifiziert
  (nächster praktischer Schritt).

## 10. Repo-Layout

```
sensir/
├── .env.example
├── docker-compose.yml
├── mosquitto/                 Broker-Config (nur für IR-Quelle)
├── scripts/create_mqtt_user.sh
└── backend/
    ├── requirements.txt
    ├── alembic/                Migrationen (0001 initial, 0002 multi-source)
    ├── tests/test_normalize.py
    └── app/
        ├── config.py           Settings (.env)
        ├── db.py / models.py / schemas.py
        ├── main.py             FastAPI-App, lifespan startet Quellen+Scheduler
        ├── scheduler.py        APScheduler-Jobs
        ├── ingest/             die drei Quellen + normalize + registry
        ├── ml/window_model.py  KernelDensity-Modell
        ├── alerting/           engine.py (Regeln) + telegram.py
        ├── api/                REST-Router
        └── web/                Dashboard (Jinja2-Templates)
```

## 11. Offene nächste Schritte

1. Echte Tuya-/Shelly-Geräte anbinden, Pulsar- bzw. WebSocket-Format anhand
   der Logs verifizieren (`docker compose logs -f backend`).
2. `Sensor.config`-Felder (`room`, `private`) werden von `normalize.py`
   z. T. noch nicht ausgewertet (nur `on_threshold_w` aktiv genutzt) — bei
   Bedarf in `engine.py`/`window_model.py` berücksichtigen (z. B. private
   Räume aus Detail-Auswertungen ausschließen).
3. Telegram-Contact-Onboarding vereinfachen (Self-Service statt manuellem
   `getUpdates`-Auslesen).
4. Tuya-Cloud-Trial-Verlängerung im Kalender vormerken.
5. Gas-Erkennung für Shelly ergänzen (aktuell nur `smoke:N.alarm` gemappt).
