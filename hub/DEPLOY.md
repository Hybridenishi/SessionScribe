# Deploying scribe-hub on atomsk (H1)

A runbook for the first deploy. Each step says who does it. **Nate** does anything that involves
a credential he must see or approve; **Futaba** does the server work. Stop and report if a check
fails; don't improvise around it.

Ground rules: no secret value goes into a repo, a log, a chat or an audit post (fingerprints and
present/absent only). The hub must never push to or merge into `main` of either vault. One naota
GPU job at a time.

## 0. Before you start — check, change nothing

- [ ] `docker network ls` and `docker network inspect` show nothing using `172.30.10.0/24` or
      `172.30.11.0/24`. If either is taken, pick two free /24s and use them in every step below.
- [ ] `tailscale serve status`: 443 is open-webui's. Port **8443** must be free.
- [ ] Note the tailnet login `tailscale serve` will report for Nate (`tailscale whois` on his
      Mac's tailnet IP). It goes in `SCRIBE_TAILSCALE_LOGIN`.

## 1. Folders (Futaba)

```bash
mkdir -p /mnt/user/appdata/scribe-hub/{data,vaults,ssh,src}
chown -R 1000:1000 /mnt/user/appdata/scribe-hub      # the image runs as uid 1000 ("scribe")
chmod 700 /mnt/user/appdata/scribe-hub/ssh
```

## 2. Code (Futaba)

Clone `Hybridenishi/SessionScribe` `main` into `/mnt/user/appdata/scribe-hub/src` with read-only
access. Build from `src/hub`:

```bash
cd /mnt/user/appdata/scribe-hub/src/hub && docker build -t scribe-hub:dev .
```

## 3. The hub's own Azora-DM clone (Nate, then Futaba)

The hub never uses `/mnt/user/vaults`; it gets its own clone so it can't collide with Obsidian sync
or Futaba's checkout.

- **Nate:** add a **read-only** deploy key to `Hybridenishi/Azora-Dm` (GitHub → Settings → Deploy
  keys). Read-only is enough for H1: `SCRIBE_PUSH_BRANCHES` stays `false`.
- **Futaba:** generate that key pair inside `/mnt/user/appdata/scribe-hub/ssh` (`id_ed25519`, mode
  0600, owned by 1000), add `github.com` to `known_hosts` there, give Nate the **public** half only,
  then clone as uid 1000:

```bash
docker run --rm --user 1000:1000 -v /mnt/user/appdata/scribe-hub/ssh:/home/scribe/.ssh:ro \
  -v /mnt/user/appdata/scribe-hub/vaults:/vaults --entrypoint git scribe-hub:dev \
  clone -q git@github.com:Hybridenishi/Azora-Dm.git /vaults/Azora-DM
```

## 4. campaign.yaml (Futaba)

Nate has the real file (speaker map + vocabulary prompt). It is not in git. Put it at
`/mnt/user/appdata/scribe-hub/data/campaign.yaml`, owned by 1000. It can be edited later from the
app's Settings.

## 5. Compose (Futaba)

Copy `compose.example.yaml` to `/mnt/user/appdata/scribe-hub/compose.yaml` and fill in
`SCRIBE_TAILSCALE_LOGIN`. Leave `SCRIBE_PUSH_BRANCHES: "false"`. Then:

```bash
cd /mnt/user/appdata/scribe-hub && docker compose up -d && docker compose ps
```

- [ ] `scribe-hub` is `healthy` within a minute. If it stays `starting`/`unhealthy` and the log says
      "main listener refused a connection from 127.0.0.1", `SCRIBE_MAIN_ALLOWED_CIDRS` is missing
      `127.0.0.1/32` (the image's own health check).
- [ ] `curl -s http://127.0.0.1:8780/healthz` → `{"ok":true}`.

## 6. Check the network walls (Futaba) — do not skip

The main API only accepts connections from the `azora` network, and the GPU queue only from
`azora-iris`. This depends on how Docker forwards the published port, so prove it:

- [ ] From the host: `curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8780/healthz` → `200`
      (it arrives from the `azora` gateway, 172.30.10.1, on the first deploy it did).
      If it's `403`, read the refused source IP in `docker logs scribe-hub`
      ("main listener refused a connection from …"), add that IP's /32 to
      `SCRIBE_MAIN_ALLOWED_CIDRS`, recreate, and report what it was.
- [ ] From a throwaway container on `azora-iris`:
      `docker run --rm --network azora-iris curlimages/curl -s -o /dev/null -w '%{http_code}' http://scribe-hub:8780/healthz` → `403`.
- [ ] Same container, GPU port: `.../scribe-hub:8781/health` → `200` (or `503` if naota is busy).

## 7. Publish to the tailnet (Futaba)

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8780
```

- [ ] From Nate's Mac: `curl -s https://atomsk.humpback-koi.ts.net:8443/healthz` → `{"ok":true}`.

## 8. Pair the Mac app (Nate)

```bash
ssh atomsk-lan 'docker exec scribe-hub scribe-hub pair --device "Nate MacBook Pro"'
```

Quote the whole remote command, and keep apostrophes out of the device name: ssh hands the line to
the remote shell to parse a second time, so an unquoted `Nate's` breaks it. No `-it` is needed; the
command only prints.

In SessionScribe → Settings: hub address `https://atomsk.humpback-koi.ts.net:8443`, the code, Pair.
The Dashboard should show Hub healthy.

## 9. Mac worker token and install (Futaba, then Nate)

- **Futaba:** `docker exec scribe-hub scribe-hub token --scope worker --name mac-worker --out /data/mac-worker.token`
- **Nate:** copy it to the Mac and lock it down, then remove the server copy:

```bash
mkdir -p ~/.config/scribe-worker && scp atomsk-lan:/mnt/user/appdata/scribe-hub/data/mac-worker.token ~/.config/scribe-worker/token && chmod 600 ~/.config/scribe-worker/token && ssh atomsk-lan rm /mnt/user/appdata/scribe-hub/data/mac-worker.token
```

- **Nate (or Claude on the Mac):** install the worker in its own venv with `mlx-audio`, copy
  `mac_worker/com.natedavis.scribe-worker.plist` to `~/Library/LaunchAgents/`, fix paths, and
  `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.natedavis.scribe-worker.plist`.
- [ ] Dashboard → Mac worker "seen just now".

## 10. First real run (Nate)

Drop a Craig export (`Session 59 audio` re-zipped is fine) as a **new** session number in the app.

- [ ] It unpacks with no unmapped speakers, transcribes on the Mac, and stages
      `_INBOX/Session-NNN-Full-Transcript.md` on `scribe/session-NNN` in the hub's clone.
- [ ] `main` of `Hybridenishi/Azora-Dm` on GitHub is unchanged.

## 11. Iris through the GPU queue (after azora-iris #28 is merged and deployed)

- **Futaba:** `docker exec scribe-hub scribe-hub token --scope gpu --name iris-bot --out /data/iris-gpu.token`,
  move it into Iris's secrets folder (0600, mounted read-only), delete the hub copy.
- Recreate `iris-bot` with: network `azora-iris` added, `IRIS_LLM_BASE=http://scribe-hub:8781`,
  `IRIS_LLM_TOKEN_FILE=<that mounted path>`.
- [ ] Iris answers in `#iris-sandbox`; the Dashboard's GPU row counts the request.
- **Rollback:** set `IRIS_LLM_BASE` back to `http://192.168.7.59:8080` and recreate. ~30 s.

## Rollback (everything)

`docker compose down` in `/mnt/user/appdata/scribe-hub`, `tailscale serve --https=8443 off`.
Nothing else on atomsk depends on the hub until step 11, and step 11 has its own rollback.
