# IMPLEMENTATION_PLAN.md

Timeline: about 4 weeks as a side project. Each phase ends with something friends can use.

## Phase 0: Manual concierge (Days 1-3)

**Goal:** learn what "relevant" means before writing code.

- [ ] Build the intake form (see questions below)
- [ ] Collect resumes from 10-15 friends
- [ ] Hand-pick 5-10 roles per friend from LinkedIn and careers pages
- [ ] Send via Telegram or WhatsApp
- [ ] Record: which links they open, apply to, and ignore

**Exit check:** at least half of friends applied to something you sent.

### Intake form questions
- Name, college, branch, graduation year
- Resume upload (PDF)
- Target roles (SDE, data, ML/AI, QA, product, other)
- Preferred locations, remote OK?
- Minimum stipend or CTC
- Top 5 skills
- One project you're proudest of (link)
- Dealbreakers (unpaid, relocation, bond, service-company roles)
- Companies to want or avoid
- Update frequency (daily or weekly)
- Telegram username
- Consent checkbox for storing the resume for matching, with deletion on request

## Phase 1: Job pipeline (Week 1)

**Goal:** a clean, verified job database.

- [ ] Repo setup, `.env`, project structure
- [ ] `companies.yaml` with 50-100 companies hiring freshers
- [ ] Fetchers for Greenhouse, Lever, Ashby
- [ ] Normalize into the `Job` schema
- [ ] Verifier rules: domain check, fee keywords, age, seniority, dedupe
- [ ] Database tables: `companies`, `jobs`, `runs`
- [ ] GitHub Actions daily cron
- [ ] Run summary logging

**Exit check:** daily run completes; at least 500 verified jobs; spot-check 30 jobs and confirm none are fake or fee-based.

## Phase 2: Profiles and matching (Week 2)

**Goal:** ranked matches for each friend.

- [ ] Resume text extraction (PDF)
- [ ] Resume Parser prompt returning schema-valid JSON
- [ ] Profile confirmation step
- [ ] Embeddings for jobs and profiles
- [ ] Hard filters plus similarity ranking plus skill-overlap boost
- [ ] CLI command to print top 10 matches for a user
- [ ] Compare against your Phase 0 hand-picked lists

**Exit check:** for at least 3 friends, 5 or more of the top 10 matches feel relevant to them.

## Phase 3: Telegram bot and explanations (Week 3)

**Goal:** friends use it without you in the loop.

- [ ] Bot with `/start`, `/profile`, `/jobs`, `/applied`, `/pause`, `/resume`, `/delete`
- [ ] Consent notice at start
- [ ] Explainer prompt: fit summary, matched skills, missing skills
- [ ] Job cards with Apply, Applied, Not relevant, Remind me buttons
- [ ] `applications` table and feedback down-weighting
- [ ] Daily digest scheduled through the Orchestrator
- [ ] Never resend a job to the same user

**Exit check:** 10 friends receive digests for 3 days in a row without manual work from you.

## Phase 4: Hardening and feedback (Week 4)

**Goal:** make it trustworthy and measure it.

- [ ] Failure alerts to your Telegram when a source fails repeatedly
- [ ] Reminder messages for "Remind me" jobs
- [ ] Verify `/delete` removes all data, including the stored resume
- [ ] Add curated Indian startup sources and a manual-submit path
- [ ] Review button-tap rates and prune bad sources or filters
- [ ] Ask friends for feedback and interview results

**Exit check:** success metrics from SPECIFICATIONS.md reviewed; decide whether to keep it as a side project or expand.

## Suggested repo structure

```
offcampus-matcher/
  AGENTS.md
  SPECIFICATIONS.md
  ARCHITECTURE.md
  IMPLEMENTATION_PLAN.md
  companies.yaml
  src/
    fetchers/        greenhouse.py  lever.py  ashby.py
    verifier.py
    resume_parser.py
    matcher.py
    explainer.py
    delivery.py
    tracker.py
    orchestrator.py
    db.py
    schemas.py
  bot/
    main.py
  .github/workflows/daily.yml
  tests/
```

## Testing checklist

- [ ] Verifier rejects fee-based, stale, and off-domain postings (unit tests with fixtures)
- [ ] Parser output always validates against the schema
- [ ] Matcher respects every hard filter
- [ ] No duplicate sends
- [ ] One failing source does not stop the run
- [ ] `/delete` leaves no user data behind

## Later options (only if demand appears)

- Web dashboard and login
- More sources and role-specific channels
- Resume feedback as a paid extra
- Employer or college dashboards
