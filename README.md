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

## Google Cloud setup (personal use)

1. Create a Google Cloud project
2. Enable the **Gmail API**
3. Configure **OAuth consent screen** (or **Google Auth Platform → Branding/Audience**)
   - Publishing status: **Testing**
   - Add your Gmail as a **Test user**
4. Create **OAuth client ID** (Web application)
   - Authorized redirect URI: `http://localhost:8085/oauth2/callback`
5. Download the JSON and save it as `client_secret.json` in the repo root (it is ignored by git).

## Configuration

- `TELEGRAM_BOT_TOKEN`
- `ENCRYPTION_KEY` (to encrypt the stored Google refresh token in sqlite)
- `GOOGLE_CLIENT_SECRETS_FILE` (path to downloaded OAuth client JSON; default: `client_secret.json`)
- `DATABASE_PATH` (optional; defaults to `bot.db`)
- `OAUTH_PORT` / `OAUTH_REDIRECT_BASE` (optional; defaults to localhost callback)

See `.env.example`.

## Running

Create a virtualenv, install deps, fill in `.env`, and run:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Create an encryption key once, then paste it into .env
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

python3 bot.py
```

## Usage

1. **`/login`**
   - The bot sends an OAuth link
   - Open it in a browser on the same machine running the bot (for `localhost` callback), sign in, approve access
   - You’ll see “Login successful”, then the bot will confirm in Telegram
2. **`/forward`**
   - Provide destination email address
   - Provide optional subject/body (`-` to skip)
   - Send documents/photos (you can forward messages from other chats)
   - Send **`/done`** to email everything as attachments

## Notes

- Only run **one** bot process per token (otherwise Telegram polling can hit `409 Conflict`).
- Do not commit `.env`, `client_secret.json`, or `bot.db`.

## Security notes

- **Never commit secrets**: bot token and email credentials must stay out of git history.
- **Revoke access if needed**: you can revoke the bot’s OAuth access in your Google account security settings.
