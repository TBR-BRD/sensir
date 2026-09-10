"""Ingest-Quellen: MQTT/IR, Tuya Cloud, Shelly Cloud.

Jede Quelle wandelt rohe Gerätezustände in kanonische SensorEvents um und
schreibt sie über app.ingest.sink in die Datenbank. Der Rest des Backends
(ML, Alerting, Status) arbeitet quellenunabhängig auf SensorEvent.
"""
