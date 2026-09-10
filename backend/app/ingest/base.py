"""Gemeinsame Basis für Ingest-Quellen."""

from __future__ import annotations

import abc
import logging
import threading
import time

logger = logging.getLogger(__name__)


class Source(abc.ABC):
    name: str = "source"

    @abc.abstractmethod
    def start(self) -> None: ...

    @abc.abstractmethod
    def stop(self) -> None: ...


class PollingSource(Source):
    """Quelle mit eigenem Hintergrund-Thread, der periodisch pollt.

    Unterklassen implementieren `connect()` und `poll_once()`. Ein optionaler
    Echtzeit-Stream kann zusätzlich in `start()` gestartet werden.
    """

    poll_interval: float = 120.0

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._running = False

    def connect(self) -> None:  # optional
        return

    @abc.abstractmethod
    def poll_once(self) -> None: ...

    def start(self) -> None:
        try:
            self.connect()
        except Exception:  # noqa: BLE001
            logger.exception("%s: connect fehlgeschlagen", self.name)
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=f"ingest-{self.name}", daemon=True)
        self._thread.start()
        logger.info("Ingest-Quelle '%s' gestartet", self.name)

    def _loop(self) -> None:
        while self._running:
            try:
                self.poll_once()
            except Exception:  # noqa: BLE001
                logger.exception("%s: poll fehlgeschlagen", self.name)
            for _ in range(int(self.poll_interval)):
                if not self._running:
                    break
                time.sleep(1)

    def stop(self) -> None:
        self._running = False
