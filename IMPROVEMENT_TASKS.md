# RecruTorr Improvement Task Sheet

Scope: combine the requested job-discovery, response-time, and user-personalization improvements. The existing SQLite/PostgreSQL profile JSON remains the source of user preferences; external API keys and paid infrastructure are not assumed.

## Ordered work

- [x] **Add configurable RSS and community submissions**
  - Added opt-in RSS 2.0/Atom ingestion via `rss_sources.yaml`, with HTTPS feed URLs and per-feed allowed application domains.
  - Added `/submitjob` and admin-only `/reviewjobs`; user submissions remain pending until a moderator inspects the live page, and approval runs the existing scam/seniority rules before publishing.
  - Added canonical application-URL deduplication across ATS, RSS, and submitted jobs; `/delete` removes pending submitter records and anonymizes approved records.
  - Configure `ADMIN_TELEGRAM_IDS` in the bot environment. Added public We Work Remotely category feeds for programming, design, DevOps, product, customer support, and sales/marketing; add only feeds you are permitted to use.
- [x] **Broaden job-source coverage**
  - Added a paginated SmartRecruiters public postings adapter alongside Greenhouse, Lever, and Ashby, and enabled more verified Greenhouse company boards (Cloudflare, Datadog, Twilio, Reddit).
  - Keep source failures isolated, normalize jobs to the existing schema, and validate apply links through the verifier.
  - Documented how to add verified company board slugs. Workable is deferred because its public endpoint behavior could not be verified; avoid guessing endpoints or scraping services with restrictive terms.
- [x] **Make preferences more interactive**
  - Expanded role choices; users can enter custom job titles and cities from `/preferences`.
  - Added remote/hybrid/on-site, employment type, and daily/weekly/on-demand digest preferences, plus skippable post-consent setup and a preferences summary.
  - Preferences are stored in profile JSON and applied as match filters.
  - Support user-entered city names and remote/hybrid/on-site preferences, but defer exact kilometer-radius filtering. The user chose not to add an external geocoder or approximate distance with a limited city-coordinate list.
- [x] **Learn from match feedback**
  - Added reason choices for irrelevant matches and a positive “more like this” button.
  - Persisted the feedback in the existing application record and use it as a soft ranking signal without changing hard preferences.
- [x] **Reduce initial and recurring latency**
  - Fetch sources concurrently (up to five at once) and log each source's fetch and verification durations; each adapter retains its request timeout.
  - `/jobs` now starts one shared background refresh on an empty cache and sends matches when available instead of waiting on the command request.
  - Deterministic fit notes and resume parsing are the defaults. LLM use is explicit opt-in with an 8-second request timeout and retries disabled.
- [x] **Validate and document**
  - Added focused coverage for the new adapter, preferences and matching, feedback ranking, digest cadence, and concurrent/cold-cache refresh behavior.
  - Updated README setup and preference guidance; validated the full test suite and Python/YAML syntax.

## Progress

- [x] Task sheet and dependency-aware session checklist created.
- [x] All requested implementation tasks completed.
- [x] Hybrid-source feature validation complete: 43 tests passed; compilation, YAML parsing, and `git diff --check` passed.
