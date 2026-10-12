# AGENTS.md

Working name: **OffCampus Matcher**
Goal: deliver verified, relevant off-campus openings to final-year students from their resume.

## Design principle

Use an LLM only where judgment or language understanding is needed. Everything else is plain deterministic code. Each "agent" below is a module with a single job, a typed input, and a typed output.

| # | Agent | Uses LLM? | Trigger |
|---|-------|-----------|---------|
| 1 | Source Fetcher | No | Daily cron |
| 2 | Verifier | No (rules) | After fetch |
| 3 | Resume Parser | Yes | On signup / resume update |
| 4 | Matcher | Embeddings only | After verify, per user |
| 5 | Explainer | Yes | After match, top-N only |
| 6 | Delivery | No | Daily digest / on demand |
| 7 | Tracker & Feedback | No | Button taps |
| 8 | Orchestrator | No | Cron + events |

---

## 1. Source Fetcher

- **Job:** Pull raw postings from configured sources.
- **Input:** `companies.yaml` (name, ats_type, board_slug, careers_url).
- **Output:** list of `RawJob` records.
- **Sources:** Greenhouse, Lever, Ashby public job-board JSON endpoints; a few hand-curated careers pages.
- **Rules:** respect robots.txt and rate limits; never scrape LinkedIn; retry with backoff; log failures per source.

## 2. Verifier

- **Job:** Decide whether a posting is authentic and suitable for freshers.
- **Input:** `RawJob`.
- **Output:** `Job` with `verified: bool`, `reject_reason`, `fresher_friendly: bool`.
- **Rules (deterministic):**
  - Apply URL domain must be the company's own domain or its ATS domain.
  - Reject if it asks for fees, training payments, or deposits (keyword list).
  - Reject if posted more than 30 days ago or marked closed.
  - Flag senior roles (3+ years, "senior", "lead", "staff") as not fresher-friendly.
  - Deduplicate by (company, normalized title, location).

## 3. Resume Parser

- **Job:** Convert a resume PDF into structured data.
- **Input:** PDF text + intake form answers.
- **Output:** `Profile` JSON (see SPECIFICATIONS.md).
- **LLM prompt contract:** return JSON only, matching the schema; unknown fields are `null`, never guessed.
- **Safety:** validate against schema; on failure, retry once, then fall back to form answers only.

## 4. Matcher

- **Job:** Rank verified jobs for each user.
- **Steps:**
  1. Hard filters: role family, location or remote, grad year, minimum pay, dealbreakers, avoided companies, already-seen jobs.
  2. Embedding similarity between profile summary and job description.
  3. Small rule-based boosts: skill overlap count, project relevance, recency.
- **Output:** top 20 `(job_id, score)`; top 5-10 go to the Explainer.

## 5. Explainer

- **Job:** Write a short, honest fit note per match.
- **Input:** `Profile` + `Job`.
- **Output:** `{fit_summary, matched_skills[], missing_skills[]}` (one or two lines each).
- **Rules:** only reference skills present in the resume or job text; no invented claims; no numeric "match %" unless it is computed from skill overlap.

## 6. Delivery

- **Job:** Send the daily digest through Telegram.
- **Output:** one message per job with inline buttons: **Apply**, **Applied**, **Not relevant**, **Remind me**.
- **Rules:** max 10 jobs per day; quiet hours; never resend a job.

## 7. Tracker & Feedback

- **Job:** Record user actions and learn from them.
- **Writes:** `applications` status, reminder times, and "not relevant" reasons.
- **Feedback use:** rejected jobs lower the weight of similar roles or companies for that user in later matching.

## 8. Orchestrator

- **Job:** Run the pipeline in order, handle failures, and keep logs.
- **Flow:** Fetch -> Verify -> (for each active user) Match -> Explain -> Deliver.
- **Rules:** one failed source must not stop the run; every run writes a summary row (jobs fetched, verified, rejected, messages sent, errors).

---

## Cross-agent rules

- All inter-agent data is validated against schemas before use.
- No agent stores or logs full resume text outside the database.
- LLM calls are capped per user per day to protect free-tier limits.
- Every job shown to a user must have `verified = true`.
