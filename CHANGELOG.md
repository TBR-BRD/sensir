# Changelog

## [Unreleased]

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
