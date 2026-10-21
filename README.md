# Telegram Torrent Creator Bot

A **private** Telegram bot that accepts large file uploads through a secure HTTPS
web interface and returns the generated `.torrent` file to your Telegram chat.

> ⚠️ **Scope**: This bot **creates torrent metadata only**. It does _not_ seed
> the torrent. To make a torrent publicly downloadable you must separately run a
> seeding client (e.g. qBittorrent) on the VPS with the source file and an
> accessible tracker or DHT.

---

## Features

| Feature | Details |
|---|---|
| Secure upload | HTTPS + `X-Upload-Key` header (constant-time compare) |
| Large file support | Streams in 1 MiB chunks – no RAM overcommit |
| Size limit | Enforced mid-stream (default 60 GiB) |
| Rate limiting | Configurable requests/minute per IP |
| Path traversal protection | `pathlib` sanitisation + resolve confinement |
| Owner-only bot | All Telegram commands silently ignore non-owners |
| Job queue | Async queue with configurable concurrency cap |
| Graceful shutdown | SIGTERM/SIGINT handling |
| Systemd service | Auto-restart on crash |
| Structured logs | Secret-redacting log filter |

---

## Project Structure

```
tg-torrent-bot/
├── bot/
│   ├── handlers.py      # /start, /status commands
│   └── notifier.py      # Send .torrent to owner
├── core/
│   ├── config.py        # pydantic-settings config
│   ├── jobs.py          # Async job queue
│   ├── torrent.py       # mktorrent wrapper
│   └── cleanup.py       # Safe file deletion
├── web/
│   ├── app.py           # FastAPI application
│   ├── auth.py          # X-Upload-Key authentication
│   └── upload.py        # Chunked streaming upload
├── tests/               # Pytest test suite
├── deploy/
│   ├── nginx.conf       # Nginx HTTPS reverse proxy config
│   └── tg-torrent-bot.service  # systemd unit
├── main.py              # Entry point
├── requirements.txt
├── .env.example
└── .gitignore
```

---

## Prerequisites

### All environments
- Python 3.12+

### VPS (Ubuntu 24.04)
- `mktorrent` – torrent file creator
- `nginx` – HTTPS reverse proxy
- A domain name with DNS pointing to the VPS (for Let's Encrypt TLS)

---

## Quick Start (Windows – Development)

### 1. Clone and set up

```powershell
cd d:\Users\sahal\Downloads\project\tg-torrent-bot

# Create virtual environment
python -m venv .venv
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt
```

### 2. Configure environment

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill in:
- `BOT_TOKEN` – from [@BotFather](https://t.me/BotFather)
- `OWNER_ID` – your numeric Telegram ID (use [@userinfobot](https://t.me/userinfobot))
- `UPLOAD_KEY` – a random secret (generate below)
- Set `UPLOAD_DIR` and `TORRENT_DIR` to local Windows paths for testing

```powershell
# Generate a secure UPLOAD_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 3. Run tests

```powershell
# Note: mktorrent tests use mocks, so no binary needed on Windows
pytest tests/ -v --tb=short
```

### 4. Start the bot (Windows – for testing only)

> Note: `mktorrent` is a Linux tool. On Windows, torrent creation will fail
> unless you install WSL with mktorrent. The web upload and Telegram delivery
> work normally without it for testing.

```powershell
python main.py
```

The upload server starts at `http://localhost:8000`.

### 5. Test file upload (curl)

```powershell
# PowerShell / curl
curl -X POST http://localhost:8000/upload `
  -H "X-Upload-Key: YOUR_UPLOAD_KEY" `
  -F "file=@C:\path\to\your\file.mkv"
```

---

## VPS Deployment (Ubuntu 24.04)

### Step 1 – Install system packages

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3.12 python3.12-venv python3-pip mktorrent nginx certbot python3-certbot-nginx
```

### Step 2 – Create dedicated user and directory

```bash
sudo useradd --system --shell /bin/false --home-dir /opt/tg-torrent-bot torrentbot
sudo mkdir -p /opt/tg-torrent-bot
sudo chown torrentbot:torrentbot /opt/tg-torrent-bot
```

### Step 3 – Upload project files

From your Windows machine, copy the project to the VPS (replace with your VPS IP):

```powershell
# Using scp from PowerShell (or use WinSCP / FileZilla)
scp -r d:\Users\sahal\Downloads\project\tg-torrent-bot\ user@YOUR_VPS_IP:/opt/tg-torrent-bot/
```

Or using rsync on WSL:
```bash
rsync -avz --exclude='.venv' --exclude='data' --exclude='.env' \
  /mnt/d/Users/sahal/Downloads/project/tg-torrent-bot/ \
  user@YOUR_VPS_IP:/opt/tg-torrent-bot/
```

### Step 4 – Set up Python virtualenv on VPS

```bash
cd /opt/tg-torrent-bot
sudo -u torrentbot python3.12 -m venv .venv
sudo -u torrentbot .venv/bin/pip install --upgrade pip
sudo -u torrentbot .venv/bin/pip install -r requirements.txt
```

### Step 5 – Configure environment on VPS

```bash
sudo cp /opt/tg-torrent-bot/.env.example /opt/tg-torrent-bot/.env
sudo nano /opt/tg-torrent-bot/.env
```

Update these fields for production:
```ini
BOT_TOKEN=YOUR_REAL_BOT_TOKEN
OWNER_ID=YOUR_REAL_TELEGRAM_ID
UPLOAD_KEY=YOUR_RANDOM_SECRET
UPLOAD_DIR=/opt/tg-torrent-bot/data/uploads
TORRENT_DIR=/opt/tg-torrent-bot/data/torrents
ENV=production
```

Lock down the .env file:
```bash
sudo chown torrentbot:torrentbot /opt/tg-torrent-bot/.env
sudo chmod 600 /opt/tg-torrent-bot/.env
```

### Step 6 – Create data directories

```bash
sudo -u torrentbot mkdir -p /opt/tg-torrent-bot/data/uploads
sudo -u torrentbot mkdir -p /opt/tg-torrent-bot/data/torrents
```

### Step 7 – Install systemd service

```bash
sudo cp /opt/tg-torrent-bot/deploy/tg-torrent-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable tg-torrent-bot
sudo systemctl start tg-torrent-bot

# Check status
sudo systemctl status tg-torrent-bot
# View logs
sudo journalctl -u tg-torrent-bot -f
```

### Step 8 – Configure Nginx

```bash
# Edit the config and replace YOUR_DOMAIN
sudo cp /opt/tg-torrent-bot/deploy/nginx.conf /etc/nginx/sites-available/tg-torrent-bot
sudo nano /etc/nginx/sites-available/tg-torrent-bot  # replace YOUR_DOMAIN

sudo ln -s /etc/nginx/sites-available/tg-torrent-bot /etc/nginx/sites-enabled/
sudo nginx -t  # test config
sudo systemctl reload nginx
```

### Step 9 – Obtain TLS certificate (Let's Encrypt)

```bash
# Point your domain's A record to the VPS IP first!
sudo certbot --nginx -d YOUR_DOMAIN
# Follow prompts – choose to redirect HTTP to HTTPS

sudo systemctl reload nginx
```

### Step 10 – Verify deployment

```bash
# Health check
curl https://YOUR_DOMAIN/health

# Test upload
curl -X POST https://YOUR_DOMAIN/upload \
  -H "X-Upload-Key: YOUR_UPLOAD_KEY" \
  -F "file=@/path/to/test.txt"

# Watch logs
sudo journalctl -u tg-torrent-bot -f
```

---

## Usage

### Telegram Commands

| Command | Description |
|---|---|
| `/start` | Show welcome message and upload URL |
| `/status` | List all jobs with their current state |

### Uploading a File

1. `POST https://YOUR_DOMAIN/upload`
2. Header: `X-Upload-Key: YOUR_UPLOAD_KEY`
3. Body: `multipart/form-data` with field `file`
4. Response: `202 Accepted` with `job_id`
5. Bot sends `.torrent` file to your Telegram when done

### Checking Job Status

```bash
curl https://YOUR_DOMAIN/status/JOB_ID \
  -H "X-Upload-Key: YOUR_UPLOAD_KEY"
```

---

## Features That Require Additional Configuration

| Feature | What you need |
|---|---|
| **HTTPS** | A domain name + Let's Encrypt cert (Step 8-9 above) |
| **Tracker** | Replace default trackers in `.env` `TRACKER_URLS` with your own tracker for private torrents |
| **Seeding** | Install qBittorrent/Transmission on VPS, point at source file + .torrent |
| **DHT/magnet links** | Requires seeding client + open ports (6881 UDP/TCP) |
| **Piece size tuning** | Set `TORRENT_PIECE_SIZE_KIB` in `.env` (e.g. `8192` for 8 MiB pieces on large files) |

---

## Security Notes

- The `UPLOAD_KEY` is never logged or included in HTTP responses
- The `BOT_TOKEN` is filtered from all log output by `_SecretFilter`
- Filename path traversal is blocked before any disk write
- All subprocess calls use argument lists — `shell=True` is never used
- The systemd service runs as an unprivileged `torrentbot` user with `NoNewPrivileges=true`

---

## Running Tests

```bash
# Activate venv first
.venv\Scripts\Activate.ps1  # Windows
# or
source .venv/bin/activate   # Linux/macOS

pytest tests/ -v --tb=short
```

All tests use mocked subprocesses and HTTP clients — no real bot token,
mktorrent binary, or network access required.

---

## Monitoring

```bash
# Systemd service status
sudo systemctl status tg-torrent-bot

# Live log stream
sudo journalctl -u tg-torrent-bot -f

# Health endpoint
curl https://YOUR_DOMAIN/health

# Disk usage of data dirs
du -sh /opt/tg-torrent-bot/data/uploads
du -sh /opt/tg-torrent-bot/data/torrents
```

---

## Updating

```bash
# On VPS
cd /opt/tg-torrent-bot
# Upload new files with scp/rsync
sudo -u torrentbot .venv/bin/pip install -r requirements.txt
sudo systemctl restart tg-torrent-bot
```

---

## License

For personal, private use only. Ensure all files you distribute are ones you
own or have explicit authorisation to distribute.
