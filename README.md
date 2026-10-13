# RecruTorr 🔍

A zero-cost, privacy-first Telegram bot that delivers authentic, verified off-campus job and internship openings matched to your resume — powered by a multi-agent AI pipeline.

---

## 🛠️ System Architecture

1. **Source Fetchers (`src/fetchers/`)**: Pulls jobs from Greenhouse, Lever, Ashby, and SmartRecruiters public job-board endpoints.
2. **Verifier (`src/verifier.py`)**: Deterministic rules rejecting scams/fees, stale postings (>30d), unauthorized domains, senior roles (3+ yrs), and duplicates.
3. **Resume Parser (`src/resume_parser.py`)**: Converts uploaded PDF resumes into structured `Profile` JSON (LLM with deterministic fallback).
4. **Matcher (`src/matcher.py`)**: Applies hard filters (role family, location/remote, dealbreakers, avoided companies) and ranks with TF-IDF similarity + skill-overlap boost + feedback penalties.
5. **Explainer (`src/explainer.py`)**: Writes honest 1-2 line fit notes (matched vs. missing skills).
6. **Delivery (`src/delivery.py`)**: Formats daily Telegram digests with interactive action buttons (**Apply**, **Applied**, **Not relevant**, **Remind me**).
7. **Tracker (`src/tracker.py`)**: Logs interactions and automatically down-weights disliked roles/companies for subsequent runs.
8. **Orchestrator (`src/orchestrator.py`)**: Coordinates the daily job-fetch pipeline and execution logging in SQLite locally or PostgreSQL when deployed.

---

## 🚀 Quickstart

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Companies
Edit `companies.yaml` to specify company names, ATS types (`greenhouse`, `lever`, `ashby`, `smartrecruiters`), board slugs/company identifiers, and allowed domains. The included config has additional Greenhouse boards (Cloudflare, Datadog, Twilio, Reddit). For SmartRecruiters, the identifier is the company slug in its career-site URL (for example, `careers.smartrecruiters.com/<identifier>`). Add only board identifiers and domains that you have verified.

### 3. Fetch & Verify Jobs
Run the daily fetch and verification pipeline:
```bash
python -m src.run_fetch
```
This populates `data.db` with verified fresher-friendly jobs.

### 4. Run the Telegram Bot
1. Create a bot with [@BotFather](https://t.me/BotFather) on Telegram and get your API token.
2. Set the token and run:
```bash
# Windows PowerShell
$env:TELEGRAM_BOT_TOKEN="your_token_here"
python -m bot.main

# Linux / macOS
export TELEGRAM_BOT_TOKEN="your_token_here"
python -m bot.main
```

3. In Telegram:
   - Send `/start` and accept the consent notice.
   - Optionally set role, city, work-mode, job-type, and digest-frequency preferences in the guided setup. `/preferences` lets you edit them later and add custom cities or job titles. Weekly digests arrive on Monday in India Standard Time; on-demand mode disables automatic digests but keeps `/jobs` available.
   - Upload your resume PDF directly in the chat to extract your profile and skills.
   - Run `/jobs` to browse matched openings with fit notes and action buttons.
   - Tap **Not relevant** and choose a reason, or use **More like this**, to tune future recommendations.
   - Check `/applied` to view all jobs you have marked as applied.
   - Run `/delete` anytime to delete all your personal data.

### Free deployment on Render

This setup uses a Render free web service, a free Neon PostgreSQL database, and GitHub Actions for scheduled work. It avoids Render's paid worker and persistent disk. Free services can sleep, so Telegram webhooks and scheduled tasks may be delayed while Render wakes the service.

1. Create a free PostgreSQL project at [Neon](https://neon.com/) and copy its connection string (use the pooled connection string and keep its SSL parameters).
2. Push this repository to GitHub, then in Render choose **New + → Blueprint** and select this repository's `main` branch. Apply the included `render.yaml`; confirm that the service plan is **Free**.
3. In Render's `recru-torr-bot` environment settings, set:
   - `DATABASE_URL`: the Neon connection string.
   - `TELEGRAM_BOT_TOKEN`: your token from [@BotFather](https://t.me/BotFather).
   - `WEBHOOK_SECRET`: a random secret containing only letters, numbers, `_`, or `-`.
   - `SCHEDULER_SECRET`: a separate random secret.
   - `OPENAI_API_KEY` is optional. To avoid per-match network delays, fit notes use deterministic rules by default. Set `LLM_EXPLANATIONS_ENABLED=true` to opt in to model-generated fit notes. Resume parsing remains heuristic by default; set `LLM_RESUME_PARSING_ENABLED=true` to opt in. Optional LLM requests have an 8-second timeout and no automatic retries; use a model available to your provider.
4. After the first successful deploy, copy the service's public URL, such as `https://recru-torr-bot.onrender.com`.
5. In GitHub, open **Settings → Secrets and variables → Actions → New repository secret** and add:
   - `RENDER_APP_URL`: the Render public URL, without a trailing slash.
   - `SCHEDULER_SECRET`: exactly the same value set in Render.
6. Run **Actions → Daily OffCampus Matcher Pipeline → Run workflow**, select `digest`, and run it once to fetch jobs and send the first digest. The schedule runs a digest daily at 09:00 IST and checks reminders hourly.
7. Open the bot in Telegram, send `/start`, accept the privacy notice, upload a PDF resume, and try `/jobs`.

Render automatically provides the public hostname used to register the Telegram webhook. Do not add `DATABASE_URL` or either secret to the repository. The free Render filesystem is temporary; user profiles, preferences, and jobs are stored in Neon instead. Neon Free currently advertises no credit card requirement, but its compute can scale to zero; Render free services can also sleep, so wake-ups and scheduled delivery are best-effort, not an always-on production guarantee.

---

## 🧪 Running Tests
```bash
python -m pytest -v
```
