import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import health, scheduler
from app.api import router as api_router
from app.config import settings
from app.ingest import registry
from app.web.routes import router as web_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# TEMPORÄR (2026-09-29): tuya-connector-python loggt Pulsar-Verbindungsfehler/
# -abbrüche nur auf DEBUG-Level (siehe TuyaOpenPulsar._on_error/_on_close) -
# damit unsichtbar bei unserem sonst auf INFO stehenden Root-Logger. Für die
# Fehlersuche "Kamera meldet Bewegung, sensir bekommt nichts" hochgestellt,
# danach wieder entfernen (siehe app/ingest/tuya.py TUYA-PULSAR-DEBUG2).
logging.getLogger("tuya iot").setLevel(logging.DEBUG)


@asynccontextmanager
async def lifespan(app: FastAPI):
    registry.start_all()
    scheduler.start()
    yield
    scheduler.stop()
    registry.stop_all()


app = FastAPI(title="SensIR", lifespan=lifespan)
if settings.cors_origins:
    # Nur für die (lesenden) /api-Endpunkte gebraucht, z. B. damit eine
    # Home-Assistant-Lovelace-Karte im Browser die Status-API direkt
    # abfragen kann. Leer per Default - siehe CORS_ALLOW_ORIGINS in .env.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=["*"],
    )
app.mount("/static", StaticFiles(directory="app/web/static"), name="static")
app.include_router(api_router)
app.include_router(web_router)


@app.get("/healthz", include_in_schema=False)
def healthz(response: Response):
    """Für Docker HEALTHCHECK (Dockerfile) + den autoheal-Sidecar in
    docker-compose.yml - meldet 503, sobald der Heartbeat-Scheduler-Job zu
    lange nicht mehr lief (siehe app/health.py), damit ein stilles
    Einfrieren des Prozesses automatisch einen Neustart auslöst, statt
    unbemerkt stundenlang liegen zu bleiben."""
    age = health.seconds_since_heartbeat()
    if not health.is_healthy():
        response.status_code = 503
        return {"status": "unhealthy", "heartbeat_age_seconds": round(age)}
    return {"status": "ok", "heartbeat_age_seconds": round(age)}
