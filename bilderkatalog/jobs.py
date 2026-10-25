"""Einfache Hintergrundaufgaben (Indexierung, Export) mit Fortschrittsanzeige."""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any


class Job:
    def __init__(self, kind: str):
        self.kind = kind
        self.state = "idle"        # idle | running | done | error | cancelled
        self.message = ""
        self.done = 0
        self.total = 0
        self.result: Any = None
        self._cancel = threading.Event()
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.state == "running"

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def update(self, message: str | None = None, done: int | None = None, total: int | None = None) -> None:
        with self._lock:
            if message is not None:
                self.message = message
            if done is not None:
                self.done = done
            if total is not None:
                self.total = total

    def start(self, target: Callable[["Job"], Any]) -> bool:
        with self._lock:
            if self.state == "running":
                return False
            self.state, self.message, self.done, self.total, self.result = "running", "", 0, 0, None
            self._cancel.clear()
        threading.Thread(target=self._run, args=(target,), daemon=True, name=f"job-{self.kind}").start()
        return True

    def _run(self, target: Callable[["Job"], Any]) -> None:
        try:
            self.result = target(self)
            self.state = "cancelled" if self.cancelled() else "done"
        except InterruptedError as err:
            self.state, self.message = "cancelled", str(err)
        except Exception as err:  # Fehler in der Oberfläche anzeigen statt Thread still sterben lassen
            traceback.print_exc()
            self.state, self.message = "error", str(err)

    def to_dict(self) -> dict:
        with self._lock:
            return {
                "kind": self.kind, "state": self.state, "message": self.message,
                "done": self.done, "total": self.total, "result": self.result,
            }
