# email-tele-bot

Telegram bot that forwards files and images sent in Telegram to an email address, using a prior login (Google OAuth2 + Gmail API).

## Commands

- **`/login`**: authenticate the sender Gmail account using **Google OAuth2** (one-time browser consent).
- **`/forward`**: asks for destination email (validated by regex), subject (optional), body (optional), then accepts documents/photos (including forwarded messages) and sends them as email attachments.
- **`/done`**: (during `/forward`) send the email.
- **`/cancel`**: cancel the current flow.

## Prerequisites

- **Telegram bot token**: create a bot via BotFather and get `TELEGRAM_BOT_TOKEN`.
- **Google OAuth client**: create an OAuth client ID and download the `client_secret.json` file (kept private).
- **Python**: 3.10+ recommended.

## Local run (quick)

Create a virtualenv, install deps, fill in `.env`, and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create an encryption key once, then paste it into .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

python3 bot.py
```

## Cloud Run deploy (short)

### 1) Project + APIs

```bash
gcloud auth login
gcloud config set project <YOUR_PROJECT_ID>
gcloud services enable run.googleapis.com cloudbuild.googleapis.com secretmanager.googleapis.com
```

Also in Google Cloud Console:
- Link billing to the project.
- Enable Gmail API.
- Configure OAuth consent screen (Testing) and add your Gmail as Test user.
- Create OAuth client (Web application).
- Download `client_secret.json`.

### 2) Store OAuth client secret

```bash
gcloud secrets create gmail-oauth-client --data-file=client_secret.json
```

If it already exists:

```bash
gcloud secrets versions add gmail-oauth-client --data-file=client_secret.json
```

```bash
PROJECT_ID="$(gcloud config get-value project)"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

gcloud secrets add-iam-policy-binding gmail-oauth-client \
  --member="serviceAccount:$SA" \
  --role="roles/secretmanager.secretAccessor"
```

### 3) Create `cloudrun.env`

```env
TELEGRAM_BOT_TOKEN=...
ENCRYPTION_KEY=...
GOOGLE_CLIENT_SECRETS_FILE=/secrets/client_secret.json
BASE_URL=https://replace-after-first-deploy.run.app
OAUTH_REDIRECT_BASE=https://replace-after-first-deploy.run.app
TELEGRAM_WEBHOOK_PATH=/telegram/webhook/<long-random-string>
DATABASE_PATH=bot.db
LOG_LEVEL=INFO
```

### 4) Deploy

```bash
gcloud run deploy telegram-email-bot \
  --source . \
  --region asia-southeast1 \
  --allow-unauthenticated \
  --min-instances=0 \
  --env-vars-file cloudrun.env \
  --set-secrets=/secrets/client_secret.json=gmail-oauth-client:latest
```

### 5) Get URL + final OAuth redirect

```bash
gcloud run services describe telegram-email-bot \
  --region asia-southeast1 \
  --format='value(status.url)'
```

Use that URL to update:
- `BASE_URL`
- `OAUTH_REDIRECT_BASE`
- OAuth redirect URI in Google Cloud Console:
  - `https://<service-url>/oauth2/callback`

Redeploy once after updating `cloudrun.env`.

### 6) Verify

```bash
curl https://<service-url>/healthz
```

Then test in Telegram: `/login` then `/forward`.

## Notes

- Only run **one** bot process per token (otherwise Telegram polling can hit `409 Conflict`).
- Do not commit `.env`, `cloudrun.env`, `client_secret.json`, or `bot.db`.
- Keep `ENCRYPTION_KEY` stable; changing it makes stored tokens unreadable.

## Troubleshooting (Cloud Run)

- **Generic startup port error**
  - Usually means app crashed before binding. Check revision logs:
  ```bash
  gcloud logging read 'resource.type="cloud_run_revision" AND resource.labels.service_name="telegram-email-bot"' --project="$(gcloud config get-value project)" --limit=100 --order=desc
  ```
- **`python: can't open file '/app/server.py'`**
  - A secret was mounted at `/app` and hid code. Mount only at `/secrets/...`.
- **`OAuth is not configured yet`**
  - `GOOGLE_CLIENT_SECRETS_FILE` path mismatch or secret mount missing.
- **`redirect_uri_mismatch`**
  - OAuth client redirect URI does not exactly match Cloud Run URL callback.
- **`403 access_denied` (app not verified)**
  - OAuth app in testing; add your Gmail under Test users.

## Code structure

- `bot.py`: Telegram command flow and bot handlers (`/login`, `/forward`, `/done`, `/cancel`).
- `server.py`: Cloud Run HTTP entrypoint (webhook endpoint + OAuth callback route + health check).
- `oauth_server.py`: Google OAuth URL generation and callback/token exchange logic.
- `email_client.py`: Gmail API send logic (build MIME message + send via Gmail API).
- `storage.py`: sqlite encrypted credential storage (refresh tokens per Telegram user).
- `config.py`: environment variable loading and config defaults.
- `validators.py`: input validation helpers (email regex checks).

### Common edit points

- Change conversation text/flow: `bot.py`
- Change email send behavior/headers/attachments: `email_client.py`
- Change OAuth callback/login behavior: `oauth_server.py`
- Change deployment/webhook behavior: `server.py`
- Add new persisted fields/tables: `storage.py`
- Add or rename env vars: `config.py` (and update `.env.example` / `cloudrun.env`)

## Improvements

- Save previously used recipient emails per user and show a quick-select list in `/forward`.
- Customize message replies at each stage for users.
- Clean up directory structure to follow better practices for bot development.

