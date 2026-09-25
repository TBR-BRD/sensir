# Changelog

## [Unreleased]

### sensir-card v1.3.0: Kacheln starten zugeklappt, kleiner Auf-/Zu-Pfeil

Feedback nach v1.2.0: die immer sichtbare Ereignisliste war zu viel auf
einen Blick. Kacheln zeigen jetzt standardmäßig nur den Status; ein kleiner
Pfeil rechts klappt die letzten Ereignisse separat auf/zu (Zustand pro
Karte, kein Neuladen). Klick auf den Haushaltsnamen öffnet weiterhin die
volle sensir-Seite in neuem Tab — beides jetzt getrennte Klickflächen
innerhalb derselben Kachel statt einem einzigen `<a>` über die ganze Karte.

### sensir-card v1.2.0: letzte Ereignisse statt Sensoren/Kontakte

Auf Nutzerwunsch die aufklappbare Sensoren/Kontakte-Ansicht aus v1.1.0
wieder entfernt — stattdessen zeigt jede Haushalts-Kachel direkt (kein
Klick nötig) die letzten `event_limit` Ereignisse (Standard 10), und ein
Klick auf die Kachel öffnet wieder die vollständige sensir-Seite in einem
neuen Tab (wie in v1.0.0).

- Neuer Endpunkt `GET /api/households/{id}/events?limit=` — die
  Ereignis-Abfrage aus der Web-Detailseite (`app/web/routes.py`) nach
  `status_service.recent_events()` extrahiert und geteilt, statt dupliziert.

### sensir-card v1.1.0: Sensoren/Kontakte direkt in der Karte aufklappbar

Klick auf eine Haushalts-Kachel öffnete bisher extern die volle sensir-Seite
(neuer Tab, Kontextwechsel raus aus Home Assistant). Jetzt klappt ein Klick
Sensoren + Kontakte direkt in der Karte auf; der externe Link bleibt als
Fallback für Aktionen, die die Karte bewusst nicht dupliziert (Zeitfenster
bearbeiten, Testnachricht senden, Sensor anlegen).

- `GET /api/sensors` bekommt einen optionalen `?household_id=`-Filter
  (vorher immer alle Sensoren aller Haushalte).

### Home Assistant Lovelace-Karte (`www/sensir-card.js`)

Neue Custom Card für Home Assistant: Ampel-Übersicht aller sensir-Haushalte
direkt in einem HA-Dashboard, analog zur sensir-eigenen Web-Oberfläche.
Fragt die sensir-REST-API direkt aus dem Browser ab (kein eigener
Home-Assistant-Custom-Component nötig), pollt periodisch neu, Klick öffnet
die sensir-Haushaltsseite. Setup/Konfiguration siehe `sensir.md` 9.1.

- `CORS_ALLOW_ORIGINS` (neue Setting in `app/config.py`/`.env`): Komma-Liste
  erlaubter Browser-Origins, per FastAPI `CORSMiddleware` nur auf GET
  beschränkt — leer per Default (kein CORS).

### Wöchentlicher CSV-Datenexport per Telegram (für eigenes ML-Training)

Neues `app/export.py`: einmal pro Woche (Standard: sonntags 5 Uhr UTC,
`WEEKLY_EXPORT_*`-Settings) bekommt jeder aktive Haushalt eine CSV mit allen
Sensor-Rohereignissen der letzten 7 Tage per Telegram (`sendDocument`) an
alle aktiven Kontakte mit `telegram_chat_id` geschickt — Grundlage für
eigenes ML-Training außerhalb des eingebauten KernelDensity-Modells.

- CSV-Format (eine Zeile pro Ereignis): `received_at_utc`,
  `received_at_local`, `sensor_id`, `sensor_name`, `sensor_kind`,
  `sensor_external_id_or_topic`, `event_kind`, `value`, `safety`.
- `POST /api/households/{id}/export?days=7` löst den Export sofort aus
  (Testen/außerhalb des Wochenrhythmus), statt auf den Scheduler zu warten.
- `alerting/telegram.py`: neue `send_telegram_document()`.
- Jeder Versand landet in `alert_log`, analog zur Kontakt-Testnachricht.

### Dashboard: Auto-Refresh, Zeitfenster-Formular korrigiert

- **Auto-Refresh**: alle Seiten laden sich per `<meta http-equiv="refresh">`
  alle 60s automatisch neu (`base.html`, per Page mit `{% block refresh %}`
  überschreibbar/abschaltbar) — vorher musste man manuell neu laden, um
  neue Ereignisse/Alarme zu sehen.
- **Fix Zeitfenster-Formular verschoben**: die "Zeitfenster hinzufügen"-
  Formularfelder standen in anderer Reihenfolge als die Tabellenspalten
  darüber (z. B. Wochentag-Auswahl unter "Min. Aktionen") und sahen dadurch
  verrutscht aus, obwohl Formular und Tabelle technisch unabhängig sind.
  Felder umsortiert (Wochentag/Von/Bis/Min. Aktionen) und mit `<label>`
  versehen, damit es auch ohne zufällige Spalten-Ausrichtung eindeutig ist.

### Fix: Zeitzone bei Ereignis-Anzeige + Sensor-Name in der Tabelle

`SensorEvent.received_at` liegt in der DB als UTC, wurde im Dashboard aber
ohne Umrechnung auf die Haushalts-Zeitzone angezeigt — live beobachtet:
Shelly-App zeigte für eine Bewegung 20:09 Lokalzeit, das Dashboard 18:10
(dasselbe Ereignis, 2h Differenz durch fehlende UTC→Lokalzeit-Umrechnung,
sah aus wie eine veraltete/verpasste Auswertung). Fix zentral in
`status_service.compute_status()` (`last_event_at`) und in der
Haushalts-Detailseite (`recent_events`).

Außerdem: die Tabelle "Letzte Ereignisse" (vorher "Letzte
Fernbedienungs-Ereignisse") zeigt jetzt den **Sensornamen** und die
**Ereignis-Art** (`kind`) pro Zeile — vorher nur `protocol`/`data_hex`,
die außerhalb der IR-Bridge-Quelle immer leer waren und bei Tuya/Shelly-
Ereignissen eine leere Tabelle vortäuschten.

### Fix: Tuya-Geräte-Discovery (`/v1.0/users/{uid}/devices` abgeschaltet)

`GET /api/sources/tuya/devices` lieferte immer `[]`: der genutzte Endpunkt
`/v1.0/users/{uid}/devices` existiert für neuere Tuya-Projekte (Space-
Berechtigungsmodell) nicht mehr — `code 1106 "permission deny"`, auch mit
korrekter UID (per Tuya-API-Explorer verifiziert). Umgestellt auf die
projektbezogene `GET /v2.0/cloud/thing/device` (mit Pagination,
`page_size<=20`), braucht keine App-Account-UID mehr. Gefunden und live
gegen ein echtes Tuya-Konto verifiziert.

### Notrufknopf: sofortiger Alarm

- Neues `Sensor.config["emergency"] = true` — jedes Ereignis dieses Sensors
  gilt als `safety=True` (wie Rauch/Gas), unabhängig vom erkannten `kind`.
- Sicherheitsalarme (Rauch/Gas, jetzt auch Notruftaster) lösen jetzt sofort
  beim Empfang aus (`send_immediate_safety_alert`, aus
  `app.ingest.sink.record_events` aufgerufen), statt bis zu
  `CHECK_INTERVAL_MINUTES` auf den nächsten periodischen Scheduler-Tick zu
  warten. Der periodische Check bleibt als Fallback-Sicherheitsnetz.
- `PATCH /api/sensors/{id}` ergänzt (fehlte bisher — nur GET/POST/DELETE
  waren da), zum Ändern von `name`/`config`/`is_active` ohne den Sensor
  löschen und neu anlegen zu müssen.

### MQTT-Broker: extern statt selbstgehostet

Die IR-Bridge-Quelle braucht einen Broker, den sowohl die Tasmota-Geräte in
den (mehreren, räumlich getrennten) Haushalten als auch das Backend erreichen
— ein per Docker Compose auf der Synology mitgehosteter Mosquitto ist dafür
ungeeignet, da dort kein Port-Forwarding eingerichtet ist und mehrere externe
Haushalte ihn ohnehin nicht erreichen könnten.

- `docker-compose.yml`: `mosquitto`-Service entfernt, `backend` verbindet
  sich direkt (ausgehend) zu einem extern konfigurierten Broker über
  `MQTT_HOST`/`MQTT_PORT` aus `.env`.
- Repo aufgeräumt: `mosquitto/` (Config/Datenverzeichnisse) und
  `scripts/create_mqtt_user.sh` entfernt (waren nur für den lokalen Broker).
- Ein gemeinsamer MQTT-Benutzer für alle Haushalte, Trennung über
  eindeutige `mqtt_topic`-Namen pro Sensor — **keine** Broker-ACLs, siehe
  `sensir.md` Abschnitt 9 für die daraus resultierende Sicherheitslücke.
- README/`sensir.md` (Abschnitte 2, 4.1, 7, 8, 9, 10) entsprechend
  aktualisiert.

### Mehrere Haushalte / Telegram-Kontakte: Pause, Priorität, Testnachricht, CRUD

Architektur für den Mehr-Haushalt-Betrieb mit gemeinsamem Tuya-/Shelly-Konto
abgerundet, plus Synology-taugliche Volume-Planung.

- **`Household.is_active`** (Migration `0003_household_active`, Default
  `true`): pausiert Zeitfenster-Auswertung, ML-Training und alle Alarme
  (inkl. Rauch/Gas-Sofortalarme) für einen Haushalt, ohne ihn oder seine
  Historie zu löschen (z. B. während eines Klinikaufenthalts). Umschalten
  über Dashboard-Button oder `PATCH /api/households/{id}`.
- **`Contact.priority`**: Kontaktlisten (API + Dashboard) sind jetzt danach
  sortiert (0 = zuerst benachrichtigt). Vorerst reine Sortierung — bei einem
  Alarm werden weiterhin alle aktiven Kontakte gleichzeitig benachrichtigt,
  keine Eskalationsstufen.
- **Kontakt-Testnachricht:** `POST /api/households/{id}/contacts/{id}/test`
  (und Dashboard-Button) schickt sofort eine Telegram-Testnachricht an einen
  Kontakt, unabhängig von Zeitfenstern, protokolliert in `alert_log`.
- **CRUD abgerundet:** `PATCH`/`DELETE` jetzt auch für Haushalte, Kontakte
  und Beobachtungsfenster (`api/households.py`, `api/contacts.py`,
  `api/windows.py`); manuelles Bearbeiten eines Fensters setzt
  `source=manual`.
- **Dashboard:** Pause-Toggle und "(pausiert)"-Kennzeichnung je Haushalt,
  gedimmte Karten für pausierte Haushalte (`.status-paused`), Prioritäts-Feld
  im Kontaktformular, "Testnachricht senden"-Button pro Kontakt.
- **Docker-Volumes → Bind-Mounts:** `postgres`- und `ml_models`-Daten liegen
  jetzt unter `./data/postgres` bzw. `./data/ml_models` statt in benannten
  Docker-Volumes, damit Synology Hyper Backup/Snapshot Replication den
  Projektordner als Ganzes sichern kann. Siehe `sensir.md` Abschnitt 8.1 für
  die empfohlene Ordnerstruktur und die Migrationsanleitung vom alten
  Named-Volume-Stand.

### Multi-Source-Backend

Das Backend ist von "nur IR-Bridge" auf **drei austauschbare Sensor-Quellen**
umgebaut. Ein Deployment, ein `config`/`.env`, ein ML-Modell über alle Sensoren
eines Haushalts.

- **Datenmodell:** `IrEvent` → generisches `SensorEvent` (`kind`, `value`,
  `safety`). `Sensor` bekommt `kind` (`ir_bridge`/`tuya`/`shelly`),
  `external_id` (Cloud-Geräte-ID), `config` (JSON), `last_seen_at`.
  `mqtt_topic` ist jetzt optional. Migration `0002_multi_source`.
- **Ingest-Schicht** (`app/ingest/`):
  - `registry.py` startet die per ENV aktiven Quellen.
  - `mqtt_ir.py` — die bisherige MQTT/Tasmota-Anbindung, jetzt als Quelle.
  - `tuya.py` — Tuya Cloud: Status-Polling + Pulsar-Echtzeit-Stream.
  - `shelly.py` — Shelly Cloud: Status-Polling + Cloud-WebSocket.
  - `normalize.py` — Tuya-/Shelly-Statuscodes → kanonische Ereignisse,
    Flankenerkennung für Steckdosen, Rauch/Gas → `safety`.
- **Alerting:** `engine.py` prüft weiterhin die Beobachtungs-Zeitfenster
  (jetzt über alle `SensorEvent`s), plus **sofortige Rauch-/Gas-Alarme**
  unabhängig vom Zeitfenster.
- **ML:** `window_model.py` unverändert im Prinzip, zählt nun alle
  Nicht-Safety-`SensorEvent`s.
- **API:** `POST /api/sensors` akzeptiert `kind`/`external_id`/`config`;
  neu `GET /api/sources/{tuya,shelly}/devices` (Cloud-Geräte-Discovery),
  `DELETE /api/sensors/{id}`.
- **Config/Deploy:** `TUYA_*` / `SHELLY_*` / `MQTT_ENABLED` in `.env`;
  Mosquitto im Compose als optional markiert.

### Offen

- Pulsar-/Shelly-WebSocket-Nachrichtenformat gegen echte Geräte verifizieren.
- Tuya-Trial-Kontingent verlängern (läuft nach ~1 Monat ab).
- Tests für Ingest/Normalize.
