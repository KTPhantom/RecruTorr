# ARCHITECTURE.md

## 1. Overview

A small batch pipeline plus a Telegram bot. No frontend. Everything runs on free tiers.

```
                +-------------------+
                |  companies.yaml   |
                +---------+---------+
                          |
                          v
+-----------+     +---------------+     +-------------+
| GitHub    |---> | Source Fetcher|---> |  Verifier   |
| Actions   |     +---------------+     +------+------+
| (cron)    |                                  |
+-----+-----+                                  v
      |                               +--------+--------+
      |                               |   Database      |
      |                               | (Postgres/SQLite|
      |                               |  + pgvector)    |
      |                               +--------+--------+
      |                                        ^
      v                                        |
+-----+------+     +-----------+     +---------+-------+
|Orchestrator|---> |  Matcher  |---> |    Explainer    |
+-----+------+     +-----------+     +---------+-------+
      |                                        |
      |                                        v
      |                               +--------+--------+
      +-----------------------------> |    Delivery     |
                                      +--------+--------+
                                               |
                                               v
                                      +--------+--------+
        Resume PDF ---> Resume Parser |  Telegram Bot   | <--- Student
        (on signup)                   +-----------------+
```

## 2. Components

| Component | Tech | Notes |
|-----------|------|-------|
| Scheduler | GitHub Actions cron | Free; runs daily pipeline |
| Bot server | Python + `python-telegram-bot` | Webhook on a free host, or long polling on a small VM |
| Database | Supabase Postgres + pgvector (or SQLite for the first week) | Stores users, jobs, matches, applications |
| File storage | Supabase Storage | Resume PDFs, private bucket, deletable |
| LLM | One provider with a free or cheap tier | Parsing and explanations only |
| Embeddings | Provider embedding API or a small local model | Jobs and profile summaries |
| Intake | Google Form or Telegram onboarding flow | Form first, bot flow later |
| Config | `companies.yaml`, `.env` | Secrets in GitHub Actions secrets |

## 3. Data flow

### A. Daily pipeline
1. Cron triggers the Orchestrator.
2. Fetcher pulls jobs from every source in `companies.yaml`.
3. Verifier applies rules and writes `verified` jobs; rejected ones are stored with a reason.
4. New jobs are embedded.
5. For each active user: hard filter, similarity ranking, remove already-sent jobs.
6. Explainer writes fit notes for the top matches.
7. Delivery sends the digest through Telegram.
8. Orchestrator writes a run summary.

### B. Signup
1. Student starts the bot and accepts the consent notice.
2. Student uploads resume and answers preference questions.
3. Resume Parser returns structured JSON.
4. Bot shows the parsed profile; student confirms or corrects.
5. Profile and embedding are stored.

### C. Feedback
1. Student taps a button on a job card.
2. Bot updates the `applications` row.
3. Feedback adjusts future ranking for that user.

## 4. Database tables

```
users(id, telegram_id, name, grad_year, consent_at, paused, created_at)
profiles(user_id, profile_json, summary_embedding, updated_at)
companies(id, name, ats_type, board_slug, careers_url, active)
jobs(id, company_id, title, location, remote, description, apply_url,
     source, posted_at, verified, reject_reason, fresher_friendly,
     embedding, first_seen_at)
matches(user_id, job_id, score, fit_json, sent_at)
applications(user_id, job_id, status, reason, updated_at)
runs(id, started_at, finished_at, fetched, verified, rejected, sent, errors_json)
```

## 5. Key design decisions

- **Telegram over a web app:** students already use it; no login, no frontend to build.
- **Public ATS endpoints over scraping:** stable, allowed, and naturally verified.
- **Rules for verification, LLM for language:** cheaper, predictable, and easier to debug.
- **Embeddings for ranking, LLM only for the final top results:** keeps cost near zero.
- **Batch pipeline over real-time:** jobs change daily, not by the minute.
- **Single database:** one place to enforce deletion and retention.

## 6. Security and privacy

- Resume files in a private bucket; access only through service key.
- Consent timestamp stored at signup.
- `/delete` removes the user row, profile, matches, applications, and resume file.
- No resume text in logs; redact personal fields in error reports.
- Secrets only in environment variables or GitHub secrets.
- Do not send full resume text to the LLM more than once per parse.

## 7. Observability

- Each pipeline run writes a `runs` row.
- Per-source fetch status logged; alert to the admin's Telegram chat if a source fails three days in a row.
- Track: jobs fetched, verified rate, messages sent, button-tap rates.

## 8. Scaling path (only if it grows)

1. Move bot to a managed host with a queue for sends.
2. Add a simple web dashboard and login.
3. Add more sources and city or role channels.
4. Add employer-side features and a paid tier.
