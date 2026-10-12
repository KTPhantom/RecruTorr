# SPECIFICATIONS.md

## 1. Purpose

A side project that helps final-year students find authentic off-campus job and internship openings matched to their resume, delivered through Telegram. First users are the builder's friends during campus placement season.

## 2. Users

- **Student:** uploads a resume, sets preferences, receives matches, tracks applications.
- **Admin (builder):** manages company sources, monitors pipeline health, handles deletion requests.

## 3. Scope

### In scope (v1)
- Intake form and resume upload
- Daily verified job fetch from public ATS endpoints
- Resume parsing, matching, short fit explanations
- Telegram bot digest with action buttons
- Basic application tracker and reminders
- Data deletion on request

### Out of scope (v1)
- Web app or login system
- Payments
- Auto-applying on behalf of users
- LinkedIn scraping
- Employer-facing features

## 4. Functional requirements

| ID | Requirement |
|----|-------------|
| F1 | Student can submit resume (PDF) and preferences through a form or the bot. |
| F2 | System extracts a structured profile from the resume and shows it for confirmation. |
| F3 | System fetches jobs daily from all configured sources. |
| F4 | Only jobs passing verification rules are ever shown. |
| F5 | Student receives up to 10 ranked jobs per day with a short fit explanation. |
| F6 | Each job has buttons: Apply link, Applied, Not relevant, Remind me. |
| F7 | System never sends the same job to the same student twice. |
| F8 | Student can view a list of jobs they marked Applied, with dates. |
| F9 | Student can delete all their data with one command (`/delete`). |
| F10 | Admin can add or remove a company source by editing `companies.yaml`. |

## 5. Non-functional requirements

- **Cost:** near zero. Free tiers only for hosting, database, and cron.
- **Reliability:** one failing source must not break the daily run.
- **Privacy:** resumes stored only for matching; deleted on request; no sharing with third parties; consent captured at signup.
- **Latency:** daily digest delivered at a fixed morning time; on-demand `/jobs` replies within 15 seconds.
- **Scale target (v1):** 50 users, 200 companies, ~5,000 active jobs.

## 6. Data schemas

### Profile
```json
{
  "user_id": "string",
  "name": "string",
  "grad_year": 2027,
  "degree": "string",
  "skills": ["python", "sql"],
  "projects": [{"title": "string", "summary": "string", "tech": ["string"]}],
  "experience": [{"org": "string", "role": "string", "months": 3}],
  "target_roles": ["sde", "ml"],
  "locations": ["bangalore", "remote"],
  "min_stipend_inr": 0,
  "dealbreakers": ["unpaid", "bond"],
  "avoid_companies": ["string"],
  "summary_embedding": "vector"
}
```

### Job
```json
{
  "job_id": "string",
  "company": "string",
  "title": "string",
  "location": "string",
  "remote": true,
  "description": "string",
  "apply_url": "string",
  "source": "greenhouse|lever|ashby|manual",
  "posted_at": "date",
  "verified": true,
  "fresher_friendly": true,
  "embedding": "vector"
}
```

### Application
```json
{
  "user_id": "string",
  "job_id": "string",
  "status": "sent|applied|not_relevant|remind",
  "reason": "string|null",
  "updated_at": "timestamp"
}
```

## 7. Verification rules

1. Apply URL host is the company's domain or a known ATS domain.
2. No fee-related keywords (registration fee, training fee, deposit, pay to join).
3. Posted within the last 30 days and not marked closed.
4. Title and description pass the fresher-friendly check (no 3+ years required).
5. Not a duplicate of an existing job.

## 8. Matching rules

1. Hard filter: role family, location or remote, grad year, minimum pay, dealbreakers, avoided companies.
2. Rank by embedding similarity plus skill-overlap boost.
3. Cap at 10 jobs per user per day.
4. Down-weight roles and companies the user marked Not relevant.

## 9. Bot commands

| Command | Behaviour |
|---------|-----------|
| `/start` | Consent notice and onboarding link |
| `/profile` | Show parsed profile |
| `/jobs` | Send today's matches now |
| `/applied` | List applied jobs |
| `/pause` / `/resume` | Stop or restart digests |
| `/delete` | Delete all user data after confirmation |

## 10. Success metrics

- At least 60% of students open the digest on most days in week 2.
- At least 1 in 5 sent jobs gets an Apply or Applied tap.
- Fewer than 5% of sent jobs marked Not relevant after tuning.
- Zero fee-based or fake postings delivered.
- Qualitative: at least a few friends report interviews from the links.

## 11. Risks

| Risk | Mitigation |
|------|------------|
| Few fresher roles on public ATS boards | Add curated Indian startup sources; allow manual submissions. |
| Resume parsing errors | Show parsed profile for confirmation. |
| LLM free-tier limits | Cap calls; use embeddings for ranking; explain only top results. |
| Privacy complaints | Clear consent, deletion command, minimal retention. |
| Source endpoint changes | Per-source error logging and alerts. |
