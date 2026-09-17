# PR Review Agent

Automated pull-request reviewer for this repository. When a PR is opened,
reopened, updated with new commits, or receives a new comment, a **headless
Hermes agent** (backed by **Argo `claude-opus-5`**) reviews the change with full
tool access — reading the diff, the codebase, and the docs, considering the PR
discussion — and posts a formal GitHub review: **Approve** or **Request
changes**.

## How it works

```
PR event → GitHub → Actions workflow on the self-hosted lambda5 runner
   → hermes chat -q "<review prompt>"   (Hermes, model = Argo claude-opus-5)
        → Hermes reads the diff + repo + docs + discussion with its tools,
          reasons about the change,
          posts APPROVE / REQUEST_CHANGES via `gh pr review`
```

- **Trigger** — `.github/workflows/pr-review.yml` fires on `pull_request`
  (`opened`, `reopened`, `synchronize`) and on `issue_comment` (`created`) for
  comments on PRs (so it re-reviews on new commits *and* new comments).
- **Runner** — a **self-hosted runner** (`self-hosted, lambda5`) inside ANL.
  Required because the model is served by a local Argo proxy on lambda5
  (`http://localhost:44497/v1`), reachable only from within ANL.
- **Reviewer** — Hermes Agent, configured with the `custom` provider pointing at
  the Argo proxy. The review instructions live in
  [`.github/pr-review-prompt.md`](pr-review-prompt.md) — edit that file to change
  the review standards.
- **Scope** — same-repository PRs only. Fork PRs are skipped by design (they do
  not receive the workflow token).

## Dependencies on lambda5 (already set up)

| Piece | Where / how |
|---|---|
| GitHub Actions runner | `~/actions-runner`, user systemd service `gh-runner.service` (linger enabled → starts on boot) |
| Hermes Agent | installed via the official installer; `hermes` on `~/.local/bin` |
| Hermes model config | provider `custom`, `model.name=argo:claude-opus-5`, `base_url=http://localhost:44497/v1`, `key_env=ARGO_API_KEY` |
| Argo token | `~/.hermes/.env` → `ARGO_API_KEY=...` |
| Argo proxy | local OpenAI-compatible proxy on `localhost:44497` **must be running** |
| `gh` CLI | `~/.local/bin/gh` (authed per-run via the workflow token) |

### Runner service

```bash
systemctl --user status gh-runner.service      # check
systemctl --user restart gh-runner.service     # restart
journalctl --user -u gh-runner.service -f       # logs
```

## Important notes / limitations

- **The Argo proxy must be running** on lambda5 (`localhost:44497`). If it is
  down, reviews fail. (Consider running it as its own user service so it, too,
  survives reboot.)
- **Identity** — reviews post as `github-actions[bot]` (the workflow token).
- **Self-approval** — GitHub forbids approving your own PR, so for PRs *you*
  author the agent falls back to posting the review as a comment instead of an
  approval.
- A bot's `REQUEST_CHANGES` does **not** satisfy a branch-protection rule that
  requires human approvals; it does visibly flag the PR and notify the author.
