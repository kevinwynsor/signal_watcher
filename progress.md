# Progress

## 2026-11 — Scheduling moved from GitHub Actions cron to cron-job.org

**Change**
- Removed `schedule:` cron trigger from [.github/workflows/watcher.yml](.github/workflows/watcher.yml).
- Workflow now triggered only via `workflow_dispatch`.
- External scheduler: **cron-job.org** calls GitHub API `workflow_dispatch` endpoint on schedule.
- `watcher.py` itself skips runs outside market hours; `force` input (bool) overrides.

**Why**
- GitHub Actions scheduled cron unreliable (delayed/skipped runs under load). cron-job.org fires on time.

**cron-job.org setup (reference)**
- URL: `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/watcher.yml/dispatches`
- Headers: `Authorization: Bearer <GitHub PAT w/ actions:write>`, `Accept: application/vnd.github+json`
- Body: `{"ref":"main"}` (optionally `{"ref":"main","inputs":{"force":"true"}}`)
- Expected response: `204 No Content`.

**Unchanged**
- Workflow: concurrency group `watcher`, state.json persisted via actions/cache, secrets `CONFIG_JSON` + `TV_SESSION`, debug.png uploaded on failure.

**To verify / TODO**
- Confirm cron-job.org interval + timezone match market hours.
- PAT expiry date — rotate before it lapses or runs silently stop.
- 2026-11-21: CI uses runner's preinstalled Chrome (`channel="chrome"` when GITHUB_ACTIONS) — removed Playwright browser cache + `install-deps` apt step (slow). Step timeout 4m, job 7m.
- 2026-11-21: `dismiss_popups()` (Esc + close-button selectors) each `--once` poll — promo overlays (Early bird offer) blocked chart/Data Window.
