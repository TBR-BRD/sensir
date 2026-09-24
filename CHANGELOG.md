# Changelog

## [Unreleased]

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
