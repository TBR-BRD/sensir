# SensIR — Gesamtübersicht

Stand: 2026-09-24. Diese Datei fasst alles zusammen, was
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
 externer MQTT-Broker     openapi.tuya*.com          shelly-*.shelly.cloud
 (öffentlich erreichbar,     + Pulsar-Stream            + Cloud-WebSocket
  eigener Server/DynDNS,
  außerhalb des Compose-
  Stacks)
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
| `households` | ein beobachteter Haushalt (Name, Zeitzone, `is_active`) |
| `sensors` | ein Gerät, einer Quelle zugeordnet (`kind`) |
| `sensor_events` | kanonisches Aktivitätsereignis (ex-`IrEvent`) |
| `observation_windows` | Zeitfenster mit Mindest-Aktionszahl, manuell oder `source=ml` |
| `activity_checks` | Ergebnis der Auswertung eines Zeitfensters für einen Tag |
| `contacts` | Angehörige mit `telegram_chat_id`, `priority` |
| `alert_log` | jede gesendete Alarm-Nachricht (auch Testnachrichten) |

### `Household` — Multi-Haushalt & Pause (`is_active`)

- Alle Haushalte teilen sich **ein** Tuya- und **ein** Shelly-Cloud-Konto
  (`TUYA_APP_ACCOUNT_UID` / `SHELLY_AUTH_KEY` sind global in `.env`,
  nicht pro Haushalt) — Zuordnung zum Haushalt passiert allein über die
  `Sensor.household_id`-Verknüpfung beim Anlegen des Sensors. Eine
  Erweiterung auf mehrere Cloud-Konten (z. B. je Elternhaus ein eigenes
  Tuya-Konto) ist bewusst **nicht** gebaut — aktuell reicht ein Konto, das
  alle beobachteten Wohnungen verknüpft hat.
- `Household.is_active` (Migration `0003_household_active`, Default `true`)
  pausiert einen Haushalt **ohne** ihn zu löschen: bei `is_active=False`
  überspringt `run_periodic_check()` (`alerting/engine.py`) den Haushalt
  komplett (keine Zeitfenster-Auswertung, keine Alarme — auch keine
  Sofort-Alarme bei Rauch/Gas, siehe `_check_safety` im selben Loop) und
  `train_all_household_models()` (`ml/window_model.py`) trainiert kein
  Modell für ihn. Events werden weiter aufgezeichnet (Sensoren senden
  unverändert), nur die Auswertung ruht — gedacht für z. B. einen
  Klinikaufenthalt der beobachteten Person.
- Umschalten: Button "Überwachung pausieren"/"fortsetzen" im
  Haushalts-Dashboard (`POST /households/{id}/toggle-active`), oder
  `PATCH /api/households/{id}` mit `{"is_active": false}`.
- Dashboard-Karte zeigt pausierte Haushalte gedimmt mit grauem Rand
  (`.status-paused` in `style.css`) statt der Ampelfarbe.

### `Contact.priority`

- Niedrigere Zahl = wird zuerst benachrichtigt, `0` = primärer Kontakt.
  Kontaktlisten (API und Dashboard) sind nach `priority` sortiert.
- **Wichtig:** aktuell nur eine Sortierreihenfolge, **keine Eskalationsstufen**
  — bei einem Alarm werden weiterhin alle aktiven Kontakte gleichzeitig
  benachrichtigt (`_notify_contacts()` in `alerting/engine.py`), niemand wird
  übersprungen oder verzögert. `priority` ist Vorarbeit für eine spätere
  Eskalation (z. B. "erst Tochter, nach 10 Min ohne Reaktion auch Nachbarin"),
  die noch nicht implementiert ist.
- Testnachricht pro Kontakt: Button "Testnachricht senden" im
  Haushalts-Dashboard (`POST /households/{id}/contacts/{id}/test`) bzw.
  `POST /api/households/{id}/contacts/{id}/test` — schickt sofort eine
  Telegram-Nachricht an genau diesen Kontakt und protokolliert sie in
  `alert_log`, unabhängig von Zeitfenstern/Aktivität. Nützlich beim
  Einrichten, um die `telegram_chat_id` zu verifizieren.

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

**`config["emergency"] = true`** — markiert einen Sensor (typischerweise
einen Taster) als Notrufknopf: **jedes** seiner Ereignisse gilt als
`safety=True`, unabhängig vom erkannten `kind`. Löst — anders als
Rauch/Gas, die ebenfalls `safety=True` sind — **sofort** beim Empfang einen
Alarm aus (`app.alerting.engine.send_immediate_safety_alert`, aufgerufen
direkt aus `app.ingest.sink.record_events`), statt bis zum nächsten
periodischen Scheduler-Tick zu warten — für einen Notrufknopf wäre eine
Verzögerung von bis zu `CHECK_INTERVAL_MINUTES` inakzeptabel. Der
periodische `_check_safety()`-Lauf bleibt als Fallback-Sicherheitsnetz
bestehen (z. B. falls der Sofort-Alarm wegen eines Backend-Neustarts
mittendrin durchrutscht), entprellt über denselben `AlertLog`-Marker.

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
- **Broker: extern, nicht Teil des Compose-Stacks.** Die beobachteten
  Haushalte sind räumlich getrennt (verschiedene Wohnungen der Eltern/
  Angehörigen), keiner davon hat Port-Forwarding zum Hosting-Server
  eingerichtet — ein lokal in `docker-compose.yml` mitgehosteter Broker
  wäre für die IR-Bridges der anderen Haushalte gar nicht erreichbar.
  Stattdessen zeigen sowohl alle Tasmota-Geräte als auch dieses Backend
  (als MQTT-Client, ausgehende Verbindung) auf einen von außen
  erreichbaren Broker — hier: ein selbst betriebener Broker hinter einer
  DynDNS-Adresse (AVM MyFRITZ!, Port-Forwarding auf der jeweiligen
  FritzBox). **Ein gemeinsamer MQTT-Benutzer für alle Haushalte** — die
  Trennung der Haushalte passiert ausschließlich über eindeutige
  `mqtt_topic`-Namen pro Sensor (z. B. `sensir-<haushalt>-01`), nicht über
  Broker-ACLs. Siehe Abschnitt 9 für die daraus resultierende
  Sicherheitslücke (jeder mit den MQTT-Zugangsdaten kann jedes Topic
  lesen/fälschen).
- **Setup:**
  1. Tasmota flashen/konfigurieren, MQTT-Server/Port/Zugangsdaten auf den
     externen Broker zeigen lassen (Werte aus `.env`: `MQTT_HOST/PORT/
     USERNAME/PASSWORD`) — **nicht** auf die Synology.
  2. Sensor anlegen:
     ```bash
     curl -X POST localhost:8000/api/sensors -H 'Content-Type: application/json' \
       -d '{"household_id":1,"name":"Wohnzimmer","kind":"ir_bridge","mqtt_topic":"sensir-01"}'
     ```
- Konfiguration: `MQTT_ENABLED`, `MQTT_HOST/PORT/USERNAME/PASSWORD`,
  `MQTT_RESULT_TOPIC_FILTERS` (Standard `tele/+/RESULT,stat/+/RESULT`).

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
- **Bekannte Falle:** das Tuya-Trial-Kontingent (*Cloud Develop Base Resource
  Trial*, unterliegt der *IoT Core*-Subscription) läuft nach ca. einem Monat
  ab und muss im Tuya-Projekt kostenlos verlängert werden — sonst antworten
  die REST-Calls mit `code 28841002` ("No permissions. Your subscription to
  cloud development plan has expired.") und der Pulsar-Websocket mit
  `401 Unauthorized`. Verlängern: Projekt → **Service API** → „IoT Core" →
  **View Details** → bei der `IoT Core`-Zeile auf **„Extend Trial Period"**.
  Erinnerung im Dashboard: `TUYA_TRIAL_EXPIRES` in `.env` nach jeder
  Verlängerung nachtragen (siehe Abschnitt 7) — dann warnt das Dashboard
  `TUYA_TRIAL_WARN_DAYS_BEFORE` Tage vorher.
- **Pulsar-Verschlüsselung:** im Tuya-Projekt unter **Message Service**
  muss „Message Queue" aktiviert und als **Encryption Algorithm** **AES-ECB**
  gewählt werden (nicht das von Tuya empfohlene AES-GCM) — die verwendete
  `tuya-connector-python`-Bibliothek (Version 0.1.2) entschlüsselt Pulsar-
  Nachrichten nur im ECB-Modus (`app/ingest/tuya.py`, extern in der
  gepinnten Library). Ohne aktivierten Message-Service-Eintrag lehnt der
  Pulsar-Server die Verbindung mit `401 Unauthorized` ab.
- **Geräte-Discovery-Endpunkt geändert:** `list_cloud_devices()`
  (`GET /api/sources/tuya/devices`) nutzte ursprünglich
  `/v1.0/users/{uid}/devices` (Geräte eines per QR-Code verknüpften
  App-Kontos) — dieser Endpunkt existiert für neuere Tuya-Projekte im
  "Space"-Berechtigungsmodell nicht mehr und antwortet mit
  `code 1106 "permission deny"`, **auch mit korrekter UID** (per Tuya-
  API-Explorer verifiziert, nicht nur unserem Code). Funktionierender
  Ersatz: die projektbezogene `GET /v2.0/cloud/thing/device` (paginiert,
  `page_size` max. ~20 — größere Werte liefern `code 40000904 "param size
  too much"`), braucht **keine** App-Account-UID mehr. `TUYA_APP_ACCOUNT_UID`
  bleibt trotzdem nötig für den QR-Code-Verknüpfungsschritt beim Einrichten
  (Abschnitt 4.2 Setup-Schritt 2), nur nicht mehr fürs Geräte-Listing danach.
- Live gegen ein echtes Tuya-Konto verifiziert (2026-09-24): REST-Polling,
  Pulsar-Stream und Geräte-Discovery funktionieren mit obigen Einstellungen.

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
  (`DEFAULT_WINDOW_START/END/MIN_ACTIONS`, Standard 18:00–23:59, 2 Aktionen).
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
- `send_telegram_document(chat_id, filename, content, caption)` — schickt
  eine Datei (`sendDocument`, multipart), genutzt vom Wochenexport (5.4).
- Bot bei [@BotFather](https://t.me/BotFather) anlegen, Token in
  `TELEGRAM_BOT_TOKEN`.
- `Contact.telegram_chat_id` wird aktuell **manuell** ermittelt: Person
  schreibt dem Bot einmal, `chat_id` über
  `https://api.telegram.org/bot<TOKEN>/getUpdates` auslesen. Kein
  Self-Service-Onboarding (bewusste PoC-Lücke, siehe README).

### 5.4 Wöchentlicher CSV-Rohdaten-Export (`app/export.py`)

Für eigenes ML-Training außerhalb von sensir (das eingebaute Modell in
5.2 ist bewusst nur ein 1D-KernelDensity über die Tageszeit) exportiert
`export_and_send_all_households()` einmal pro Woche pro aktivem Haushalt
alle `SensorEvent`s der letzten 7 Tage als CSV und schickt sie per Telegram
(`sendDocument`) an alle aktiven Kontakte mit `telegram_chat_id`.

- **Format: CSV**, eine Zeile pro Ereignis. Gewählt statt JSON/Parquet, weil
  es ohne zusätzliche Abhängigkeiten direkt in pandas/Excel/Numbers lesbar
  ist und für die zu erwartende Datenmenge (paar hundert Events/Woche/
  Haushalt) reicht — bei Bedarf trivial in jedes andere Format konvertierbar.
- **Spalten**: `received_at_utc`, `received_at_local` (Haushalts-Zeitzone),
  `sensor_id`, `sensor_name`, `sensor_kind`, `sensor_external_id_or_topic`,
  `event_kind`, `value`, `safety`.
- **Dateiname**: `sensir_<haushalt-slug>_<von>_<bis>.csv`.
- **Zeitplan**: `WEEKLY_EXPORT_ENABLED` (Standard `true`),
  `WEEKLY_EXPORT_DAY_OF_WEEK` (APScheduler-Cron-Format, Standard `sun`),
  `WEEKLY_EXPORT_HOUR_UTC` (Standard `5`).
- **Sofort auslösen** (zum Testen oder außerhalb des Wochenrhythmus):
  `POST /api/households/{id}/export?days=7`.
- Jeder Versand wird in `alert_log` protokolliert (Erfolg/Misserfolg pro
  Kontakt), analog zur Kontakt-Testnachricht.

## 6. API-Referenz (`/api`, siehe auch `/docs` für Swagger)

| Endpunkt | Methode | Zweck |
|---|---|---|
| `/api/households` | GET/POST | Haushalte |
| `/api/households/{id}` | PATCH/DELETE | Haushalt ändern (u. a. `is_active`) / löschen (Cascade auf Sensoren, Events, Kontakte, Fenster) |
| `/api/sensors` | GET/POST | Sensoren (GET optional `?household_id=`); POST braucht `kind` + (`mqtt_topic` **oder** `external_id`) |
| `/api/sensors/{id}` | PATCH/DELETE | Sensor ändern (`name`, `config`, `is_active` - **nicht** `kind`/`mqtt_topic`/`external_id`, dafür löschen+neu anlegen) / löschen |
| `/api/sources/tuya/devices` | GET | Tuya-Cloud-Geräte des verknüpften Kontos |
| `/api/sources/shelly/devices` | GET | Shelly-Cloud-Geräte des Kontos |
| `/api/households/{id}/contacts` | GET/POST | Kontakte (Liste sortiert nach `priority`) |
| `/api/households/{id}/contacts/{id}` | PATCH/DELETE | Kontakt ändern (Name, `priority`, `telegram_chat_id`, `is_active`, …) / löschen |
| `/api/households/{id}/contacts/{id}/test` | POST | sofortige Telegram-Testnachricht an diesen Kontakt, protokolliert in `alert_log` |
| `/api/households/{id}/windows` | GET/POST | Beobachtungsfenster |
| `/api/households/{id}/windows/{id}` | PATCH/DELETE | Fenster ändern (setzt `source=manual`) / löschen |
| `/api/households/{id}/status` | GET | aktueller Status (letztes Ereignis, Tagesereignisse, aktives Fenster, letzter Check, `household_active`) |
| `/api/households/{id}/export` | POST | löst sofort einen CSV-Datenexport aus (`?days=7`), statt auf den wöchentlichen Scheduler-Job zu warten (5.4) |

Dashboard (kein API, HTML): `/` (Ampel-Übersicht, pausierte Haushalte gedimmt),
`/households/{id}` (Detail — Pause-Toggle, Kontakt-Testnachricht,
Priorität-Feld im Kontaktformular).

## 7. Konfiguration (`.env`, siehe `.env.example`)

```dotenv
# Postgres
POSTGRES_USER=sensir
POSTGRES_PASSWORD=change-me
POSTGRES_DB=sensir
DATABASE_URL=postgresql+psycopg2://sensir:change-me@postgres:5432/sensir

# Quelle 1: IR-Bridge über MQTT (externer, öffentlich erreichbarer Broker -
# siehe 4.1, kein Compose-Service)
MQTT_ENABLED=true
MQTT_HOST=your-broker.example.com
MQTT_PORT=1883
MQTT_USERNAME=change-me
MQTT_PASSWORD=change-me
MQTT_RESULT_TOPIC_FILTERS=tele/+/RESULT,stat/+/RESULT

# Quelle 2: Tuya/SmartLife
TUYA_ENABLED=false
TUYA_ACCESS_ID=
TUYA_ACCESS_SECRET=
TUYA_REGION=eu
TUYA_APP_ACCOUNT_UID=
TUYA_PULSAR_ENABLED=true
TUYA_TRIAL_EXPIRES=              # z.B. 2027-03-24, siehe Abschnitt 4.2 "Bekannte Falle"
TUYA_TRIAL_WARN_DAYS_BEFORE=14

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
DEFAULT_WINDOW_END=23:59
DEFAULT_MIN_ACTIONS=2
ML_MIN_SAMPLES=200
ML_TRAIN_HOUR_UTC=3

WEEKLY_EXPORT_ENABLED=true
WEEKLY_EXPORT_DAY_OF_WEEK=sun
WEEKLY_EXPORT_HOUR_UTC=5
```

## 8. Deployment

Zielsystem: **Synology DS720+** über Docker/Container Manager.

```bash
cp .env.example .env        # ausfüllen (u. a. MQTT_HOST/PORT des externen Brokers)
docker compose up --build
```

- `docker-compose.yml`: `postgres` + `backend`. Kein Mosquitto-Service mehr
  im Stack — der MQTT-Broker für die IR-Bridge-Quelle läuft extern (siehe
  4.1), erreichbar über `MQTT_HOST/PORT` in `.env`.
- Migrationen laufen beim Start automatisch (`alembic upgrade head` im
  Backend-Container-Command).
- Dashboard: `http://<synology>:8000/`, API-Doku: `http://<synology>:8000/docs`.

### 8.1 Volume-/Pfad-Planung auf der Synology

Bewusst **Bind-Mounts statt benannter Docker-Volumes** (Umstellung von
`pgdata:`/`ml_models:`-Named-Volumes) — alle persistenten Daten liegen
sichtbar unter dem Projektordner, statt in Docker's interner
`/var/lib/docker/volumes/…`-Verwaltung. Grund: Synology **Hyper Backup**
und **Snapshot Replication** sichern Ordner/Freigaben, nicht einzelne
Docker-Volumes — mit Bind-Mounts reicht es, den kompletten Projektordner
in den Backup-Plan aufzunehmen.

**Empfohlene Struktur** (Projektordner auf einem Datenverzeichnis-Volume,
z. B. `/volume1/docker/sensir`):

```
/volume1/docker/sensir/          ← Repo-Checkout, in Hyper Backup aufnehmen
├── .env                         ← Zugangsdaten, NICHT in Git (u. a. externer
│                                    MQTT-Broker: MQTT_HOST/PORT/USER/PW)
├── docker-compose.yml
└── data/                        ← s. .gitignore, alles hier ist Laufzeitdaten
    ├── postgres/                ← Postgres-Datenverzeichnis (die eigentlichen
    │                              Events/Historie aller Haushalte — wichtigster
    │                              Ordner fürs Backup)
    └── ml_models/                ← *.joblib, ein File pro Haushalt, aus den
                                     Events reproduzierbar (nice-to-have-Backup,
                                     kein Muss — trainiert bei Bedarf neu)
```

- **Backup-Priorität:** `data/postgres/` ist die einzige Quelle der Wahrheit
  (Events, Haushalte, Kontakte, Konfiguration) — unbedingt in den Hyper-Backup-
  Plan aufnehmen. `data/ml_models/` kann fehlen, ohne dass Daten verloren
  gehen (nächstes nächtliches Training baut es neu auf, sobald wieder genug
  Events da sind).
- **Volume-Wahl auf der DS720+:** Projektordner auf das Daten-Volume legen
  (nicht das System-Volume, falls die DS720+ mit SSD-Cache/getrenntem System-
  Volume läuft) — bei Standard-Konfiguration mit einem Volume ist das ohnehin
  identisch. Freigabe `docker` (oder `docker/sensir` als Unterordner) reicht
  als Snapshot-Replication-Ziel.
- **Mehrere Haushalte teilen sich diesen einen Stack**: es gibt keine
  Pro-Haushalt-Ordnertrennung — alle Haushalte liegen als Zeilen in
  derselben Postgres-Instanz (`households`-Tabelle), das hält die
  Volume-Planung einfach (ein Datenbank-Ordner, ein Backup-Ziel, egal wie
  viele Haushalte/Kontakte über das Dashboard verwaltet werden).
- **Migration vom alten Named-Volume-Stand:** falls bereits mit
  `pgdata`/`ml_models`-Named-Volumes deployed, vor dem Update einmalig
  `docker run --rm -v sensir_pgdata:/from -v $(pwd)/data/postgres:/to alpine cp -a /from/. /to/`
  (analog für `ml_models`), dann `docker compose up -d` mit dem neuen
  `docker-compose.yml`.

### Entwicklung ohne Docker

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

## 9. Sicherheit / bekannte Lücken

- **MQTT**: läuft jetzt über einen von außen erreichbaren Broker (nötig, weil
  IR-Bridges in mehreren, per Port-Forwarding nicht erreichbaren Haushalten
  stehen) — aktuell Benutzername/Passwort ohne TLS, und **ein gemeinsamer
  Broker-User für alle Haushalte** ohne Topic-ACLs (PoC-Entscheidung, siehe
  4.1). Das heißt: wer die MQTT-Zugangsdaten kennt, kann Topics jedes
  Haushalts lesen und fälschte Ereignisse einspielen. Für den produktiven
  Rollout: MQTTS (TLS) und entweder Client-Zertifikat pro Sensor (Tasmota
  unterstützt das nativ) oder zumindest Broker-ACLs, die einen Haushalt auf
  sein eigenes Topic-Präfix beschränken.
- **Tuya/Shelly-Zugangsdaten** liegen nur in `.env` (nicht im Git,
  `.gitignore` deckt das ab).
- **Contact-Onboarding**: `telegram_chat_id` manuell ermittelt, kein
  Self-Service-Flow.
- **ML-Wraparound**: siehe 5.2.
- **Tuya-Trial**: monatliche Verlängerung im Tuya-Dashboard nötig — Dashboard
  zeigt eine Erinnerung, sobald `TUYA_TRIAL_EXPIRES` gepflegt ist (Abschnitt 4.2).
- **Pulsar-/Shelly-WS-Format**: noch nicht gegen echte Geräte verifiziert
  (nächster praktischer Schritt).

## 9.1 Home Assistant Lovelace-Karte (`www/sensir-card.js`)

Eigene Custom Card, zeigt eine Ampel-Übersicht aller sensir-Haushalte direkt
in einem Home-Assistant-Dashboard — analog zur sensir-eigenen Web-Oberfläche,
aber innerhalb von HA. Fragt die sensir-REST-API **direkt aus dem Browser**
ab (kein eigener Home-Assistant-Custom-Component/Sensor nötig), pollt alle
`refresh_seconds` (Standard 60) neu. Klick auf eine Haushalts-Kachel klappt
**Sensoren + Kontakte direkt in der Karte** auf (`GET /api/sensors?
household_id=`, `GET /api/households/{id}/contacts`) — kein Verlassen von
Home Assistant nötig; ein Link am Ende der aufgeklappten Ansicht öffnet bei
Bedarf die vollständige sensir-Seite (Zeitfenster bearbeiten, Testnachricht
senden, Sensor anlegen, …) in einem neuen Tab.

**Setup:**
1. **CORS auf dem sensir-Server freischalten** — sonst blockt der Browser
   die Cross-Origin-Requests von der HA-Instanz zur sensir-API:
   ```dotenv
   CORS_ALLOW_ORIGINS=http://192.168.42.179:8123
   ```
   (Origin der jeweiligen HA-Instanz, kommagetrennt bei mehreren.)
2. `www/sensir-card.js` nach `/config/www/sensir-card/sensir-card.js` auf dem
   HA-Server kopieren (SSH-Add-on, `scp`/`sftp` deaktiviert — Pattern:
   `ssh hassio@<ha-host> 'sudo tee /config/www/sensir-card/sensir-card.js' < www/sensir-card.js`).
3. Als Lovelace-Ressource eintragen: **Einstellungen → Dashboards → oben
   rechts (⋮) → Ressourcen → Ressource hinzufügen** —
   URL `/local/sensir-card/sensir-card.js?v=1`, Typ „JavaScript-Modul“.
   (Cache-Busting: bei jedem Karten-Update die `?v=`-Zahl hochzählen.)
4. Karte zu einem Dashboard hinzufügen: **Dashboard bearbeiten → Karte
   hinzufügen → „SensIR“** (oder manuell per YAML, siehe unten).

**Konfiguration:**
```yaml
type: custom:sensir-card
base_url: http://192.168.42.132:8000   # erforderlich - Adresse des sensir-Servers
title: SensIR                          # optional
refresh_seconds: 60                    # optional
household_ids: [1, 2]                  # optional, sonst alle Haushalte
```

## 10. Repo-Layout

```
sensir/
├── .env.example
├── docker-compose.yml         Kein Mosquitto-Service — MQTT-Broker läuft
│                              extern (siehe Abschnitt 4.1)
└── backend/
    ├── requirements.txt
    ├── alembic/                Migrationen (0001 initial, 0002 multi-source, 0003 household.is_active)
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
