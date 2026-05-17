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
2. In Railway dashboard → "New Project" → "Deploy from GitHub repo" → select
   `jerry861200/insurance-summarizer`.
3. Railway auto-detects:
   - `requirements.txt` → installs Python deps via nixpacks
   - `nixpacks.toml` → installs `tesseract-ocr` + `poppler-utils` system packages
   - `Procfile` → runs `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - `railway.json` → uses `/healthz` for healthcheck
4. Set environment variables in Railway dashboard (Project → Variables):
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
# Expected: {"status":"ok","environment":"prod","model":"gpt-4o"}

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
# Or in dashboard: Deployments tab → click previous deploy → "Redeploy"
```
