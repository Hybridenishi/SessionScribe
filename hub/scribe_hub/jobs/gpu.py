"""The hub-wide GPU queue for naota (REBUILD-SPEC §3.3, Addendum 1 decision 2).

naota's llama-server has a single-slot KV cache, so only one request may run at a time. Every naota
model call — Iris in both modes, and anything the hub itself sends — goes through `GpuQueue.run`.
Iris reaches it as an OpenAI-compatible proxy on the hub's separate GPU listener.
"""
from __future__ import annotations

import asyncio
import time

import httpx


class GpuQueue:
    def __init__(self, base_url: str, timeout_s: float = 300, transport=None):
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._lock = asyncio.Lock()
        self._waiting = 0
        self._busy_since: float | None = None
        self._transport = transport   # tests inject an httpx.MockTransport
        self.served = 0

    def status(self) -> dict:
        return {"busy": self._busy_since is not None, "waiting": self._waiting,
                "busy_for_s": round(time.time() - self._busy_since, 1) if self._busy_since else 0,
                "served": self.served}

    async def run(self, method: str, path: str, json_body: dict | None = None) -> httpx.Response:
        """One request to naota, serialised with every other one."""
        self._waiting += 1
        waiting = True
        try:
            async with self._lock:
                self._waiting -= 1
                waiting = False
                self._busy_since = time.time()
                try:
                    async with httpx.AsyncClient(transport=self._transport,
                                                 timeout=self.timeout_s) as client:
                        resp = await client.request(method, f"{self.base_url}{path}",
                                                    json=json_body)
                    self.served += 1
                    return resp
                finally:
                    self._busy_since = None
        finally:
            if waiting:            # cancelled (e.g. client gone) before reaching the GPU
                self._waiting -= 1

    async def health(self) -> bool:
        """Not queued: a health probe does not touch the GPU."""
        try:
            async with httpx.AsyncClient(transport=self._transport, timeout=4) as client:
                r = await client.get(f"{self.base_url}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False
