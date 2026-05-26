# Daft.ie Dublin Monitor

Automated monitoring for rental listings on [Daft.ie](https://www.daft.ie) across Dublin postal districts. The system scans the Daft.ie API on a schedule, sends Telegram alerts for new listings that match your filters, and provides a Telegram bot for configuration and manual scans.

**Live bot:** [@daftirelandbot](https://t.me/daftirelandbot)  
**Render service:** `https://daft-ie-dublin-monitor.onrender.com`

---

## Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  GitHub Actions (every 30 min — free tier)                   │
│  scanner.py → Daft.ie API v2 → Telegram alerts (new only)    │
│  seen_ids.json + recent_listings.json → committed to repo    │
│  Manual scans (workflow_dispatch) → completion summary       │
└──────────────────────────────────────────────────────────────┘
                              +
┌──────────────────────────────────────────────────────────────┐
│  Render.com (free web service — 24/7)                        │
│  bot.py → Telegram webhook (production) / polling (local)    │
│  gunicorn + Flask /ping → UptimeRobot every 5 min (no sleep) │
│  Syncs state.json with GitHub; triggers Actions via /scan    │
└──────────────────────────────────────────────────────────────┘
```

| Component | Role |
|-----------|------|
| `scanner.py` | Runs in GitHub Actions; fetches listings, detects new IDs, sends alerts |
| `bot.py` | Runs on Render; handles Telegram commands, settings, and manual scan triggers |
| `state.json` | Price range, locations, date filter, favorites, notification toggle |
| `seen_ids.json` | Listing IDs already notified (prevents duplicate alerts) |
| `recent_listings.json` | Last scanned listings (used by `/list`) |
| `locations.py` | Shared Dublin district slugs and Daft geo IDs |

---

## File Structure

```
daft-monitor/
├── bot.py                          # Telegram bot (Render)
├── scanner.py                      # Listing scanner (GitHub Actions)
├── locations.py                    # Dublin district geo IDs
├── requirements.txt
├── render.yaml
├── state.json
├── seen_ids.json
├── recent_listings.json
└── .github/workflows/scan.yml
```

---

## Setup

### Step 1 — Create a Telegram Bot

1. Open Telegram and message [@BotFather](https://t.me/BotFather).
2. Send `/newbot` and follow the prompts.
3. Copy the **bot token** (e.g. `7123456789:AAFxxx...`).
4. Send any message to your new bot (e.g. "hello").
5. Open in a browser:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
6. Find `"chat":{"id":` in the JSON — that value is your **Chat ID**.

---

### Step 2 — Create the GitHub Repository

```bash
git clone https://github.com/<YOUR_USERNAME>/daft-monitor.git
cd daft-monitor
# Copy all project files into this directory
git add .
git commit -m "feat: initial daft monitor setup"
git push origin main
```

> **Note:** `seen_ids.json` is pre-seeded with existing listing IDs so the first Actions run does not spam you with alerts for listings already on Daft.ie.

---

### Step 3 — Add GitHub Secrets

Go to **Settings → Secrets and variables → Actions → New repository secret**.

| Secret | Value |
|--------|-------|
| `TELEGRAM_TOKEN` | Bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Chat ID from `getUpdates` |

Add secrets under **Actions**, not **Dependabot** or **Codespaces**. GitHub Actions automatically provides `GITHUB_TOKEN` for committing `seen_ids.json` and `recent_listings.json` — no extra secret needed for that.

---

### Step 4 — Deploy to Render

1. Go to [render.com](https://render.com) → **New → Web Service**.
2. Connect your GitHub repository.
3. Configure:
   - **Runtime:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `gunicorn --bind 0.0.0.0:$PORT --workers 1 --threads 4 --timeout 120 bot:app`
4. Add environment variables:

| Variable | Value |
|----------|-------|
| `TELEGRAM_TOKEN` | Bot token |
| `TELEGRAM_CHAT_ID` | Chat ID |
| `GITHUB_TOKEN` | GitHub Personal Access Token (`repo` scope) |
| `GITHUB_REPOSITORY` | `your-username/daft-monitor` |

5. Deploy.

**Webhook mode:** Render sets `RENDER_EXTERNAL_URL` automatically. The bot registers a webhook to that URL and does **not** use polling in production (avoids Telegram 409 Conflict). Optional: set `WEBHOOK_URL` to override, or `WEBHOOK_SECRET` for a secured `/webhook/<secret>` path.

> **Important:** Do not run `python bot.py` locally while Render is live with the same token — two instances cause a 409 Conflict. Stop the Render service or use a separate test bot for local development.

> The bot syncs `state.json` and `recent_listings.json` with GitHub. The `/scan` command triggers the GitHub Actions workflow, so `GITHUB_TOKEN` and `GITHUB_REPOSITORY` are required on Render.

---

### Step 5 — UptimeRobot (Keep Render Awake)

Render free-tier services sleep after ~15 minutes of inactivity.

1. Create a free account at [uptimerobot.com](https://uptimerobot.com).
2. **Add New Monitor:**
   - Type: **HTTP(s)**
   - URL: `https://daft-ie-dublin-monitor.onrender.com/ping`
   - Interval: **5 minutes**
3. Save.

---

### Step 6 — Verify GitHub Actions

1. GitHub → **Actions** → `Daft.ie Monitor — Scheduled Scan`
2. Click **Run workflow → Run workflow**
3. Check the job logs — on success, the workflow runs automatically every 30 minutes.

Manual runs (`workflow_dispatch`) send a Telegram completion message when no new listings are found. Scheduled cron runs do not send this summary.

---

### Step 7 — Test via Telegram

Message [@daftirelandbot](https://t.me/daftirelandbot) (or your own bot):

- `/start` — main menu with inline buttons
- `/scan` or **🔍 Hemen Tara** — trigger a manual scan
- `/help` — list all commands

After a manual scan with no new listings, you should receive:

> ✅ Tarama tamamlandı — 26 ilan kontrol edildi, yeni ilan bulunamadı.

---

## Telegram Commands

| Command | Description |
|---------|-------------|
| `/start` | Main menu with inline controls |
| `/scan` | Run an immediate Daft.ie scan via GitHub Actions |
| `/list` | Show the last scanned listings (up to 10) |
| `/setprice` | Set the monthly rent range (e.g. `1500-1800`) |
| `/setlocation` | Select or deselect Dublin districts to monitor |
| `/setdate` | Set the listing publish-date filter |
| `/toggle` | Enable or disable automatic notifications |
| `/fav` | Show saved favorite listings |
| `/help` | Show all commands |

Bot UI messages are in Turkish. Configuration is stored in `state.json` and synced to GitHub.

---

## Supported Locations

All 22 Dublin postal districts are available via `/setlocation`:

| District | District | District | District |
|----------|----------|----------|----------|
| Dublin 1 | Dublin 2 | Dublin 3 | Dublin 4 |
| Dublin 5 | Dublin 6 | Dublin 6W | Dublin 7 |
| Dublin 8 | Dublin 9 | Dublin 10 | Dublin 11 |
| Dublin 12 | Dublin 13 | Dublin 14 | Dublin 15 |
| Dublin 16 | Dublin 17 | Dublin 18 | Dublin 20 |
| Dublin 22 | Dublin 24 | | |

Default: **Dublin 6** and **Dublin 6W**. Geo IDs are defined in `locations.py`.

---

## Filters

| Filter | Default | How to change |
|--------|---------|---------------|
| Price range | €1,500 – €1,800 | `/setprice` or edit `state.json` |
| Locations | Dublin 6, Dublin 6W | `/setlocation` |
| Publish date | Last 30 days | `/setdate` (1, 7, 14, 30 days, or all) |
| Notifications | On | `/toggle` |

---

## Notification Format

Each new listing is sent as a Telegram message (photo + caption when available):

```
🏠 YENİ İLAN — Dublin 6/6W
━━━━━━━━━━━━━━━━━━━━
📍 Studio Apartment, Rathmines
💶 €1,650 per month
🏢 Studio
🛏 Studio
📅 Yayın: 25 May 2026, 09:14

🔗 Daft.ie'de Gör   [clickable link]

[⭐ Favori Ekle/Çıkar]     ← inline button
```

---

## Troubleshooting

**Bot does not respond (intermittent on Render free tier)**

- **Cold start:** After ~15 min idle, the service sleeps. The first message after wake can take **30–50 seconds** — wait and send `/start` again if needed.
- **UptimeRobot:** Monitor must ping `https://daft-ie-dublin-monitor.onrender.com/ping` every **5 minutes** (not the root URL). Wrong URL or interval >15 min lets the service sleep.
- Check Render logs: render.com → your service → **Logs** — look for `Webhook received` and `Webhook registered`.
- Visit `/ping` (fast) or `/health` (last update time + webhook errors) — should show `mode=webhook` on Render.
- Verify `TELEGRAM_TOKEN` is correct.
- Stop any local `bot.py` process — it conflicts with Render's webhook.

**Bot responds slowly after idle**

- Expected on Render **free** tier: cold start + webhook processing. UptimeRobot reduces sleep but does not remove the first-request delay after wake.
- Upgrade to a paid Render plan for always-on (no spin-down).

**Telegram 409 Conflict**

- Production uses webhook mode; old polling deployments caused this error
- Only **one** bot instance may run at a time (Render **or** local, not both)

**GitHub Actions not running**

- Confirm the workflow is enabled under **Actions**
- Verify `TELEGRAM_TOKEN` and `TELEGRAM_CHAT_ID` are set under **Secrets and variables → Actions** (not Agents or Dependabot)

**Manual scan — no completion message**

- Completion summaries are sent only for manual scans (`workflow_dispatch`), not scheduled runs
- Ensure `auto_notify` is enabled (`/toggle`)
- Check Actions logs for Telegram send errors

**Same listing notified twice**

- Confirm `seen_ids.json` was committed — check the Actions job log

**Daft API returns 403 or 0 listings**

- The scanner uses Daft.ie v2: `POST https://gateway.daft.ie/api/v2/ads/listings`
- The legacy `old/v1/listings` endpoint returns 403 (Cloudflare)
- Required headers: `brand: daft`, `platform: web`
- Location filtering uses `geoFilter.storedShapeIds` — the old `locations` filter does not work in v2
- Verify `LOCATION_GEO_IDS` in `locations.py` (Dublin 6 → `70`, Dublin 6W → `71`)
- Requests use `curl_cffi` with Chrome impersonation

**`/scan` fails or settings do not sync**

- On Render, confirm `GITHUB_TOKEN` (PAT with `repo` scope) and `GITHUB_REPOSITORY` are set

---

## Customization

Edit `state.json` for default values, or change settings at runtime via Telegram — changes are saved and committed to GitHub.

To add a new district:

1. Add an entry to `LOCATION_OPTIONS` in `locations.py`
2. Add the matching geo ID to `LOCATION_GEO_IDS`

Geo IDs can be found via Daft.ie’s location search API or the [daftlistings](https://github.com/AnthonyBloomer/daftlistings) library.
