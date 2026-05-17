# Deploy

Two paths supported:

1. **Docker (any environment)** — recommended for local / self-hosted / any cloud that runs containers. Single command, reproducible, includes the OCR system deps in the image.
2. **Railway (PaaS)** — fastest if you just want a public URL and don't want to think about containers; uses nixpacks instead of the Dockerfile.

---

## Docker (any environment)

The repo ships a `Dockerfile` and `docker-compose.yml`. The image bakes in
`tesseract-ocr` and `poppler-utils` (the system deps for OCR + PDF-to-image)
on top of `python:3.11-slim`.

### Prerequisites

- Docker Desktop (macOS / Windows) or Docker Engine (Linux)
- An `.env` file at the project root with your LLM credentials (see `.env.example`)

### Build + run

```bash
# 1. Make sure .env exists (copy from the template if you haven't already)
cp .env.example .env
# then edit .env and fill in OPENAI_API_KEY (or ANTHROPIC_API_KEY)

# 2. Build + start
docker compose up --build
```

**First build takes ~3 minutes** — most of that is `apt-get install tesseract-ocr poppler-utils` (the OCR runtime is ~150 MB on disk). Subsequent builds are seconds because Docker caches the apt layer and the pip-install layer separately from the source code.

The service is reachable at:

```bash
curl http://localhost:8000/healthz
# {"status":"ok","environment":"dev","provider":"openai","model":"gpt-4o","cross_model_verify":false}

open http://localhost:8000/docs   # Swagger UI
```

### Configuration via .env

Docker Compose reads the `.env` file at the project root (via `env_file:` in
`docker-compose.yml`). Minimum config to run with OpenAI:

```bash
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-...your-key...
OPENAI_MODEL=gpt-4o
```

Or with Anthropic:

```bash
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...your-key...
ANTHROPIC_MODEL=claude-opus-4-7
```

Optional: enable cross-model verification (requires BOTH keys) — see
`docs/architecture.md` for details:

```bash
CROSS_MODEL_VERIFY=true
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

### Persistence

The `storage/` directory is mounted as a volume in `docker-compose.yml`, so
uploaded PDFs and the SQLite DB survive `docker compose down` / restarts.
Wipe state with:

```bash
docker compose down -v       # also clears named volumes if any
rm -rf storage/pdfs/* insurance.db
```

### Run without compose

```bash
docker build -t insurance-summarizer .
docker run --rm -p 8000:8000 \
    --env-file .env \
    -v "$(pwd)/storage:/app/storage" \
    insurance-summarizer
```

---

# Deploy to Railway

The FastAPI backend can be deployed to Railway in ~5 minutes via the free tier.
SQLite + local filesystem storage works, but **note**: Railway's filesystem is
ephemeral on free tier — uploaded PDFs and the SQLite DB reset between deploys.
For persistent state, see milestone M6 (Postgres + S3) in the post-MVP roadmap.

## Prerequisites

- Railway account (free, https://railway.app)
- Either the Railway CLI (`npm i -g @railway/cli`) or the GitHub integration

## Option A: GitHub integration (easiest)

1. Push the `0516` branch (or merge it to `main`) on GitHub.
2. In Railway dashboard -> "New Project" -> "Deploy from GitHub repo" -> select
   `jerry861200/insurance-summarizer`.
3. Railway auto-detects:
   - `requirements.txt` -> installs Python deps via nixpacks
   - `nixpacks.toml` -> installs `tesseract-ocr` + `poppler-utils` system packages
   - `Procfile` -> runs `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - `railway.json` -> uses `/healthz` for healthcheck
4. Set environment variables in Railway dashboard (Project -> Variables):
   ```
   LLM_PROVIDER=openai
   OPENAI_API_KEY=sk-...your-key...
   OPENAI_MODEL=gpt-4o
   ENVIRONMENT=prod
   ```
5. Railway deploys. Visit `https://your-project.up.railway.app/healthz` to verify.

## Option B: CLI (push-to-deploy from local)

```bash
npm i -g @railway/cli            # or use the dashboard
railway login                    # opens browser
railway init                     # link a new project
railway up                       # deploy current directory
railway domain                   # get the public URL
```

Set env vars either via CLI (`railway variables set OPENAI_API_KEY=sk-...`) or
the dashboard.

## What's deployed

Just the FastAPI backend on a single Railway service. The Streamlit frontend
runs locally (or could be deployed to Streamlit Community Cloud separately):

```bash
BACKEND_URL=https://your-project.up.railway.app streamlit run frontend/app.py
```

## Verifying after deploy

```bash
URL=https://your-project.up.railway.app

# Health
curl $URL/healthz
# Expected: {"status":"ok","environment":"prod","provider":"openai","model":"gpt-4o","cross_model_verify":false}

# Swagger UI
open $URL/docs

# Sample upload (will work once OPENAI_API_KEY is set)
curl -X POST $URL/documents -F "file=@sample/leland_stanford_policy.pdf" | jq .
```

## Known limitations on Railway free tier

| Limitation | Workaround | Production fix (milestone) |
|---|---|---|
| Filesystem is ephemeral (PDFs + SQLite reset between deploys) | Re-upload after each redeploy | M6 — Postgres + S3 |
| Auto-sleeps after inactivity | First request has ~30s cold start | M5 — async + worker pool with warm pool |
| 500h/mo free quota | OK for demo/eval | Pay tier (~$5/mo for hobbyist) |
| Tesseract binary is large (~150MB) | Already handled via nixpacks.toml | Switch to AWS Textract / Azure DI (M3) if image size matters |

## Cost expectation

- Free tier: 500 execution hours/month + $5 credit. For demo use (~few requests/day), well within free tier.
- First real production: ~$5-10/mo (Hobby plan with persistent volumes).
- Real OCR cost: ~$0 (local Tesseract). Anthropic/OpenAI cost: ~$0.01-0.10 per
  document depending on model and document length.

## Rollback

```bash
railway rollback           # via CLI
# Or in dashboard: Deployments tab -> click previous deploy -> "Redeploy"
```
