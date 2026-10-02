"""scribe-hub command line, run inside the container on atomsk.

  scribe-hub serve                                   main API + GPU listener
  scribe-hub pair --device "Nate MacBook Pro"        print a one-time pairing code
  scribe-hub devices                                 list paired devices and service tokens
  scribe-hub revoke <id>                             revoke one
  scribe-hub token --scope worker --name mac-worker --out /secrets/worker.token
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import logging
import os
import sys
from pathlib import Path

from . import auth
from .config import Settings
from .db import Database


def _when(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "-"


def cmd_serve(settings: Settings, args) -> None:
    import uvicorn

    from .app import Hub, create_app, create_gpu_app

    hub = Hub(settings)
    main_cfg = uvicorn.Config(create_app(hub), host=args.host, port=args.port,
                              log_level="info", proxy_headers=False)
    gpu_cfg = uvicorn.Config(create_gpu_app(hub), host=args.gpu_host, port=args.gpu_port,
                             log_level="info", proxy_headers=False)

    async def both():
        await asyncio.gather(uvicorn.Server(main_cfg).serve(), uvicorn.Server(gpu_cfg).serve())

    asyncio.run(both())


def cmd_pair(settings: Settings, args) -> None:
    db = Database(settings.db_path)
    code = auth.create_pair_code(db, args.device, settings.pair_code_ttl_s)
    print(f"Pairing code for {args.device!r}: {code[:4]}-{code[4:]}")
    print(f"Valid for {settings.pair_code_ttl_s // 60} minutes, single use. "
          "Enter it in SessionScribe > Settings.")


def cmd_devices(settings: Settings, args) -> None:
    db = Database(settings.db_path)
    for d in auth.list_devices(db):
        state = f"revoked {_when(d['revoked_at'])}" if d["revoked_at"] else "active"
        print(f"{d['id']}  {d['scope']:<6}  {d['name']:<28}  created {_when(d['created_at'])}  "
              f"last seen {_when(d['last_seen'])}  {state}")


def cmd_revoke(settings: Settings, args) -> None:
    db = Database(settings.db_path)
    if not auth.revoke(db, args.id):
        sys.exit(f"no active device {args.id}")
    print(f"revoked {args.id}")


def cmd_token(settings: Settings, args) -> None:
    db = Database(settings.db_path)
    device_id, token = auth.create_service_token(db, args.name, args.scope)
    if args.out:
        out = Path(args.out)
        fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(token + "\n")
        os.chmod(out, 0o600)
        print(f"{args.scope} token {device_id} for {args.name!r} written to {out} (mode 0600)")
    else:
        print(f"{args.scope} token {device_id} for {args.name!r} (shown once, store it now):")
        print(token)


def main(argv=None) -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(prog="scribe-hub")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8780)
    s.add_argument("--gpu-host", default="0.0.0.0")
    s.add_argument("--gpu-port", type=int, default=8781)
    p = sub.add_parser("pair")
    p.add_argument("--device", required=True)
    sub.add_parser("devices")
    r = sub.add_parser("revoke")
    r.add_argument("id")
    t = sub.add_parser("token")
    t.add_argument("--scope", choices=["worker", "gpu"], required=True)
    t.add_argument("--name", required=True)
    t.add_argument("--out", help="write the token to this file (mode 0600) instead of printing it")
    args = ap.parse_args(argv)
    settings = Settings.from_env()
    {"serve": cmd_serve, "pair": cmd_pair, "devices": cmd_devices, "revoke": cmd_revoke,
     "token": cmd_token}[args.cmd](settings, args)


if __name__ == "__main__":
    main()
