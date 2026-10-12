# RecruTorr 🔍

A zero-cost, privacy-first Telegram bot that delivers authentic, verified off-campus job and internship openings matched to your resume — powered by a multi-agent AI pipeline.

---

## 🛠️ System Architecture

1. **Source Fetchers (`src/fetchers/`)**: Pulls jobs from Greenhouse, Lever, and Ashby public ATS endpoints.
2. **Verifier (`src/verifier.py`)**: Deterministic rules rejecting scams/fees, stale postings (>30d), unauthorized domains, senior roles (3+ yrs), and duplicates.
3. **Resume Parser (`src/resume_parser.py`)**: Converts uploaded PDF resumes into structured `Profile` JSON (LLM with deterministic fallback).
4. **Matcher (`src/matcher.py`)**: Applies hard filters (role family, location/remote, dealbreakers, avoided companies) and ranks with TF-IDF similarity + skill-overlap boost + feedback penalties.
5. **Explainer (`src/explainer.py`)**: Writes honest 1-2 line fit notes (matched vs. missing skills).
6. **Delivery (`src/delivery.py`)**: Formats daily Telegram digests with interactive action buttons (**Apply**, **Applied**, **Not relevant**, **Remind me**).
7. **Tracker (`src/tracker.py`)**: Logs interactions and automatically down-weights disliked roles/companies for subsequent runs.
8. **Orchestrator (`src/orchestrator.py`)**: Coordinates the daily automated cron pipeline and execution logging in SQLite.

---

## 🚀 Quickstart

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

### 2. Configure Companies
Edit `companies.yaml` to specify company names, ATS types (`greenhouse`, `lever`, `ashby`), board slugs, and allowed domains.

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
   - Upload your resume PDF directly in the chat to extract your profile and skills.
   - Run `/jobs` to browse matched openings with fit notes and action buttons.
   - Check `/applied` to view all jobs you have marked as applied.
   - Run `/delete` anytime to delete all your personal data.

---

## 🧪 Running Tests
```bash
python -m pytest -v
```
