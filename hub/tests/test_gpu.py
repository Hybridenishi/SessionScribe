"""The GPU queue: one naota request at a time, hub-wide (acceptance criterion 13)."""
from __future__ import annotations

import asyncio

import httpx
from fastapi.testclient import TestClient

from scribe_hub.app import Hub, create_app, create_gpu_app
from scribe_hub.jobs.gpu import GpuQueue


def test_concurrent_requests_never_overlap():
    state = {"now": 0, "max": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        state["now"] += 1
        state["max"] = max(state["max"], state["now"])
        await asyncio.sleep(0.02)
        state["now"] -= 1
        return httpx.Response(200, json={"ok": True})

    gpu = GpuQueue("http://naota.test", transport=httpx.MockTransport(handler))

    async def go():
        calls = [gpu.run("POST", "/v1/chat/completions", {}) for _ in range(6)]
        return await asyncio.gather(*calls)

    results = asyncio.run(go())
    assert all(r.status_code == 200 for r in results)
    assert state["max"] == 1 and gpu.served == 6 and gpu.status()["waiting"] == 0


def test_cancelled_waiter_does_not_leak_the_waiting_count():
    release = asyncio.Event

    async def go():
        gate = release()

        async def handler(request):
            await gate.wait()
            return httpx.Response(200)

        gpu = GpuQueue("http://naota.test", transport=httpx.MockTransport(handler))
        first = asyncio.create_task(gpu.run("POST", "/x"))
        await asyncio.sleep(0.01)
        second = asyncio.create_task(gpu.run("POST", "/x"))
        await asyncio.sleep(0.01)
        assert gpu.status()["waiting"] == 1
        second.cancel()
        await asyncio.sleep(0.01)
        gate.set()
        await first
        return gpu.status()

    assert asyncio.run(go())["waiting"] == 0


def test_gpu_listener_needs_a_gpu_token(gpu_client, gpu_headers, app_headers):
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    assert gpu_client.post("/v1/chat/completions", json=body).status_code == 401
    assert gpu_client.post("/v1/chat/completions", json=body, headers=app_headers
                           ).status_code == 401
    r = gpu_client.post("/v1/chat/completions", json=body, headers=gpu_headers)
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "ok"
    assert gpu_client.get("/health").status_code == 200


def test_streaming_is_refused(gpu_client, gpu_headers):
    r = gpu_client.post("/v1/chat/completions", json={"stream": True}, headers=gpu_headers)
    assert r.status_code == 400


def test_gpu_listener_exposes_nothing_else(gpu_client, gpu_headers):
    assert gpu_client.get("/status", headers=gpu_headers).status_code == 404
    assert gpu_client.post("/auth/pair", json={"code": "ABCDEFGH"}).status_code == 404


def test_main_api_has_no_gpu_route(settings):
    hub = Hub(settings)
    paths = set(create_app(hub, run_worker=False).openapi()["paths"])
    assert "/v1/chat/completions" not in paths and "/sessions" in paths
    assert set(create_gpu_app(hub).openapi()["paths"]) == {"/v1/chat/completions", "/health"}
    hub.db.close()


def test_status_reports_queue_and_worker(client, app_headers, worker_headers):
    client.post("/worker/claim", headers=worker_headers)
    s = client.get("/status", headers=app_headers).json()
    assert s["gpu"]["naota_healthy"] is True and s["gpu"]["waiting"] == 0
    assert s["mac_worker"]["name"] == "test-worker"
    assert s["vault"]["configured"] is True


def test_naota_down_shows_unhealthy(settings):
    def down(request):
        raise httpx.ConnectError("refused")
    hub = Hub(settings, gpu_transport=httpx.MockTransport(down))
    assert asyncio.run(hub.gpu.health()) is False
    assert TestClient(create_gpu_app(hub)).get("/health").status_code == 503
    hub.db.close()


def test_listeners_refuse_other_networks(settings):
    """TestClient connects from 'testclient', which is not an IP: outside every allowed network."""
    from dataclasses import replace
    locked = replace(settings, main_allowed_cidrs=("172.30.10.0/24",),
                     gpu_allowed_cidrs=("172.30.11.0/24",))
    hub = Hub(locked)
    assert TestClient(create_app(hub, run_worker=False)).get("/healthz").status_code == 403
    assert TestClient(create_gpu_app(hub)).get("/health").status_code == 403
    inside = TestClient(create_app(hub, run_worker=False), client=("172.30.10.1", 5000))
    assert inside.get("/healthz").status_code == 200
    iris = TestClient(create_app(hub, run_worker=False), client=("172.30.11.7", 5000))
    assert iris.get("/healthz").status_code == 403          # iris-bot's network: main API refused
    hub.db.close()
