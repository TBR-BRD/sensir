import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app import scheduler
from app.api import router as api_router
from app.config import settings
from app.ingest import registry
from app.web.routes import router as web_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


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
