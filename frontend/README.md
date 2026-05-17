# Streamlit Frontend

Upload an insurance PDF, see extracted fields with per-field confidence bars,
read the Markdown summary, inspect validation warnings.

## Quick start

```bash
# Backend (in one terminal)
cd insurance-summarizer
source venv/bin/activate
uvicorn app.main:app --port 8000 --reload

# Frontend (in another terminal)
streamlit run frontend/app.py
# Opens http://localhost:8501
```

## Pointing at a deployed backend

```bash
BACKEND_URL=https://insurance-summarizer.up.railway.app streamlit run frontend/app.py
```

## What you see

- **Sidebar:** backend health, recent uploads
- **Main panel tabs after upload:**
  - **Extracted Fields:** policy number, insured, dates, amounts — each with a
    colored confidence bar (green >= 85%, yellow 55-85%, red <55%) and an
    annotation showing whether the value came from regex or LLM
  - **Summary:** the Markdown summary
  - **Warnings:** validation warnings (info/warning/error severity)
  - **Raw JSON:** the full backend response for debugging
