"""Two FastAPI apps sharing one Hub:

- `create_app`     the main API (app + worker scopes). Published only on 127.0.0.1 and reached
                   through `tailscale serve`, which sets the Tailscale-User-Login header we check.
- `create_gpu_app` the GPU proxy alone, on its own port on the `azora-iris` network, so iris-bot
                   can reach naota through the queue and nothing else on the hub.
"""
from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import (
    auth_routes,
    campaign_routes,
    gpu_routes,
    sessions_routes,
    status_routes,
    worker_routes,
)
from .config import Settings
from .db import Database
from .jobs import queue
from .jobs.gpu import GpuQueue
from .jobs.stages import HUB_STAGES

log = logging.getLogger("scribe_hub")


class Hub:
    def __init__(self, settings: Settings, gpu_transport=None):
        self.settings = settings
        settings.archive_dir.mkdir(parents=True, exist_ok=True)
        self.db = Database(settings.db_path)
        self.gpu = GpuQueue(settings.naota_llm_base, transport=gpu_transport)
        self._wake: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def notify(self) -> None:
        """Wake the in-process worker after enqueueing a hub job. Safe from any thread."""
        if self._wake is not None and self._loop is not None:
            self._loop.call_soon_threadsafe(self._wake.set)

    def run_one_hub_job(self) -> bool:
        """Run the next queued in-process job. Returns False when the lane is empty."""
        job = queue.claim(self.db, "hub")
        if job is None:
            return False
        try:
            HUB_STAGES[job["stage"]](self, job)
        except Exception as exc:  # noqa: BLE001 — recorded on the job, never crashes the loop
            state = queue.fail(self.db, job["id"], f"{type(exc).__name__}: {exc}")
            log.exception("job %s (%s, session %s) failed -> %s",
                          job["id"], job["stage"], job["session"], state)
            if state == "failed":
                self.db.execute("UPDATE sessions SET state = 'failed', detail = ? WHERE number = ?",
                                (f"{job['stage']} failed: {exc}"[:500], job["session"]))
        return True

    async def hub_worker(self) -> None:
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        while True:
            ran = await asyncio.to_thread(self.run_one_hub_job)
            if not ran:
                self._wake.clear()
                with contextlib.suppress(asyncio.TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), timeout=5)


def _source_guard(app: FastAPI, cidrs: tuple[str, ...], name: str) -> None:
    """Refuse connections from outside the listener's own network (no-op when cidrs is empty)."""
    if not cidrs:
        return
    nets = [ipaddress.ip_network(c, strict=False) for c in cidrs]

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.client.host if request.client else ""
        try:
            ok = any(ipaddress.ip_address(host) in n for n in nets)
        except ValueError:
            ok = False
        if not ok:
            log.warning("%s listener refused a connection from %s", name, host or "unknown")
            return JSONResponse({"detail": "not reachable from this network"}, status_code=403)
        return await call_next(request)


def create_app(hub: Hub, run_worker: bool = True) -> FastAPI:
    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        recovered = queue.recover(hub.db)
        if recovered:
            log.info("re-queued %d interrupted job(s)", recovered)
        task = asyncio.create_task(hub.hub_worker()) if run_worker else None
        try:
            yield
        finally:
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    app = FastAPI(title="Azora Hub", version="0.1.0", lifespan=lifespan)
    app.state.hub = hub
    app.state.db = hub.db
    app.state.settings = hub.settings
    for r in (auth_routes, status_routes, sessions_routes, campaign_routes, worker_routes):
        app.include_router(r.router)
    _source_guard(app, hub.settings.main_allowed_cidrs, "main")
    return app


def create_gpu_app(hub: Hub) -> FastAPI:
    app = FastAPI(title="Azora Hub GPU queue", version="0.1.0")
    app.state.hub = hub
    app.state.db = hub.db
    app.state.settings = hub.settings
    app.include_router(gpu_routes.router)
    _source_guard(app, hub.settings.gpu_allowed_cidrs, "gpu")
    return app
