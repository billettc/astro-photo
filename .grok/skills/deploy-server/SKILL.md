---
name: deploy-server
description: >
  Deploy this repo to the live Astro Photo site on charles.exe.xyz and rebuild
  its Docker container. Use when the user says "deploy", "deploy to the server",
  "push to charles.exe.xyz", "update the live site", or runs /deploy-server.
---

# Deploy to charles.exe.xyz

From the repo root, run:

```bash
.grok/skills/deploy-server/scripts/deploy.sh
```

Do not look up the host, the app directory, or which files are server-local. The script already knows.

The script syncs `app/` (except its protected files), `Dockerfile`, and `requirements.txt`, then runs `docker compose up -d --build` and checks `http://127.0.0.1:8000/health` on the server. It does not upload `data/`, `.env`, `README.md`, or `docker-compose.yml`.

`--dry-run` prints the rsync plan and does not rebuild.

When the script lists a protected file that this branch changed, merge that change into the copy on the server. Do not replace the server file. The script header says what each protected file keeps.
