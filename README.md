# SensIR

"Babyphone für Senioren ohne Bild und Ton" — ein Pearl-IR-Empfänger mit
Tasmota-Firmware neben dem Fernseher meldet per MQTT, wenn die
Fernbedienung benutzt wurde. Bleibt die erwartete Aktivität in einem
Zeitfenster aus, werden Angehörige per Telegram alarmiert. Ein KI-Modell
lernt pro Haushalt den normalen Tagesablauf und ersetzt so nach und nach
die manuell konfigurierten Zeitfenster.

Hintergrund, Personas, Business Model Canvas etc. siehe die Projekt-Doku
(nicht Teil dieses Repos).

## Architektur

```
Pearl-IR-Sensor (Tasmota, "YTF IR Bridge")
        │  MQTT (tele/<topic>/RESULT, IrReceived)
        ▼
   Mosquitto Broker  ──┐
        │              │ Nutzer/Passwort-Auth, PoC-Start ohne TLS
        ▼              │
   FastAPI Backend  ◄──┘
   ├─ mqtt_listener.py    → schreibt IrEvent je empfangenem IR-Signal
   ├─ scheduler.py        → APScheduler: periodische Prüfung + nächtliches ML-Training
   ├─ alerting/engine.py  → vergleicht Ist- mit Erwartungswert, alarmiert bei Abweichung
   ├─ ml/window_model.py  → KernelDensity je Haushalt: lernt Tagesablauf, ersetzt feste Schwellen
   ├─ alerting/telegram.py→ sendet Alarme an konfigurierte Kontakte
   ├─ api/                → REST-API (Haushalte, Sensoren, Kontakte, Zeitfenster, Status)
   └─ web/                → einfaches Web-Dashboard (Ampel je Haushalt)
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
   curl -X POST localhost:8000/api/sensors -d '{"household_id": 1, "name": "Wohnzimmer", "mqtt_topic": "sensir-01"}' -H 'Content-Type: application/json'
   ```
3. Kontakte und (optional, siehe unten) Zeitfenster über das Dashboard
   unter `/households/1` anlegen.

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
