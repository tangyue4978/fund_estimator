# Fund Estimator Web

This branch is the web-only version of Fund Estimator.

## Run locally for web development

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app/Home.py
```

## Deploy to Streamlit Community Cloud

1. Push this repo to GitHub.
2. Create a new app in Streamlit Community Cloud.
3. Set the main file path to `app/Home.py`.
4. Configure `SUPABASE_URL` and `SUPABASE_KEY` in Streamlit secrets before first login.

## Notes

- This branch keeps only the online web app.
- Local packaging, desktop launcher, and mobile shell have been split to branch `local-packaging`.
- Supabase is required for auth, watchlist sync, adjustments, and daily ledger data.
- Set `SUPABASE_URL` and `SUPABASE_KEY` in `.streamlit/secrets.toml` for local development.
- Browser refresh keeps login via a signed browser cookie; the session id is no longer placed in the URL.
