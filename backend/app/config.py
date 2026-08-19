import datetime as dt

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg2://sensir:sensir@localhost:5432/sensir"

    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str = "sensir"
    mqtt_password: str = "sensir"
    mqtt_result_topic_filters: str = "tele/+/RESULT,stat/+/RESULT"

    telegram_bot_token: str = ""

    check_interval_minutes: int = 15
    default_window_start: dt.time = dt.time(18, 0)
    default_window_end: dt.time = dt.time(23, 59, 59)
    default_min_actions: int = 2

    ml_min_samples: int = 200
    ml_train_hour_utc: int = 3
    ml_model_dir: str = "app/ml/models"

    @property
    def mqtt_topics(self) -> list[str]:
        return [t.strip() for t in self.mqtt_result_topic_filters.split(",") if t.strip()]


settings = Settings()
