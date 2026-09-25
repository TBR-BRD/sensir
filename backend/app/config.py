import datetime as dt

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://sensir:sensir@localhost:5432/sensir"

    # -- MQTT / IR-Bridge-Quelle ---------------------------------------
    mqtt_enabled: bool = True
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = "sensir"
    mqtt_password: str = "sensir"
    mqtt_result_topic_filters: str = "tele/+/RESULT,stat/+/RESULT"

    # -- Tuya Cloud (SmartLife) --------------------------------------
    tuya_enabled: bool = False
    tuya_access_id: str = ""
    tuya_access_secret: str = ""
    tuya_region: str = "eu"                 # eu|us|cn|in
    tuya_app_account_uid: str = ""
    tuya_pulsar_enabled: bool = True
    # Ablaufdatum des Tuya-IoT-Core-Trial-Abos (iot.tuya.com -> Cloud -> Service
    # API -> IoT Core -> View Details -> "Extend Trial Period"). Kein API-Feld
    # dafür verfügbar - hier manuell nachtragen, wenn man verlängert hat, dann
    # zeigt das Dashboard rechtzeitig vorher eine Erinnerung (siehe
    # TUYA_TRIAL_WARN_DAYS_BEFORE).
    tuya_trial_expires: dt.date | None = None
    tuya_trial_warn_days_before: int = 14

    # -- Shelly Cloud ---------------------------------------------------
    shelly_enabled: bool = False
    shelly_auth_key: str = ""
    # aus dem Shelly-Cloud-Konto: "Einstellungen -> Autorisierungs-Cloud-Key"
    # nennt auch den Server, z. B. https://shelly-59-eu.shelly.cloud
    shelly_api_host: str = "https://shelly-eu.shelly.cloud"
    shelly_ws_enabled: bool = True

    # gemeinsames Poll-Intervall für die Cloud-Quellen (Fallback zum Stream)
    source_poll_interval_seconds: int = 120

    telegram_bot_token: str = ""

    check_interval_minutes: int = 15
    default_window_start: dt.time = dt.time(18, 0)
    default_window_end: dt.time = dt.time(23, 59, 59)
    default_min_actions: int = 2

    ml_min_samples: int = 200
    ml_train_hour_utc: int = 3
    ml_model_dir: str = "app/ml/models"

    # -- Wöchentlicher CSV-Rohdaten-Export für eigenes ML-Training ------
    # (nicht zu verwechseln mit dem eingebauten KernelDensity-Modell oben)
    weekly_export_enabled: bool = True
    weekly_export_day_of_week: str = "sun"  # APScheduler-Cron: mon..sun
    weekly_export_hour_utc: int = 5

    # Komma-getrennte Liste erlaubter Browser-Origins für CORS, z. B.
    # http://192.168.42.179:8123 für einen Lovelace-Custom-Card-Zugriff aus
    # Home Assistant. Leer (Standard) = kein CORS, nur serverseitige Zugriffe.
    cors_allow_origins: str = ""

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    @property
    def mqtt_topics(self) -> list[str]:
        return [t.strip() for t in self.mqtt_result_topic_filters.split(",") if t.strip()]

    @property
    def tuya_api_base(self) -> str:
        return {
            "eu": "https://openapi.tuyaeu.com",
            "us": "https://openapi.tuyaus.com",
            "cn": "https://openapi.tuyacn.com",
            "in": "https://openapi.tuyain.com",
        }.get(self.tuya_region, "https://openapi.tuyaeu.com")

    @property
    def tuya_pulsar_url(self) -> str:
        return {
            "eu": "wss://mqe.tuyaeu.com:8285/",
            "us": "wss://mqe.tuyaus.com:8285/",
            "cn": "wss://mqe.tuyacn.com:8285/",
            "in": "wss://mqe.tuyain.com:8285/",
        }.get(self.tuya_region, "wss://mqe.tuyaeu.com:8285/")


settings = Settings()
