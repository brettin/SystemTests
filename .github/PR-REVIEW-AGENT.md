# PR Review Agent

Automated pull-request reviewer for this repository. When a PR is opened,
reopened, updated with new commits, or receives a new comment, an agent reviews
the change and posts a formal GitHub review — **Approve** or **Request changes**.

## How it works

- **Trigger** — `.github/workflows/pr-review.yml` fires on
  `pull_request` (`opened`, `reopened`, `synchronize`) and on `issue_comment`
  (`created`) for comments on PRs.
- **Runner** — runs on a **self-hosted runner** (`self-hosted, lambda5`) inside
  ANL, because the review is powered by **Argo**, which is only reachable from
  the ANL network.
- **Reviewer** — `.github/scripts/pr_review.py` collects the PR diff, the full
  repository source, the docs, and the existing PR discussion, sends them to
  Argo, and posts back a structured review (approve vs. request-changes with a
  checklist of required changes). On re-runs it takes prior comments and new
  commits into account.
- **Scope** — same-repository PRs only. PRs from forks are skipped by design,
  because forked PRs do not receive repository secrets.

## Required repository secrets

Set these under **Settings → Secrets and variables → Actions**:

| Secret | Required | Purpose | Default if unset |
|---|---|---|---|
| `ARGO_TOKEN` | **yes** | Argo API bearer token | — |
| `ARGO_MODEL` | recommended | Argo model id (e.g. an Opus model) | `gpt4o` |
| `ARGO_URL` | no | Argo chat endpoint | native Argo chat URL |
| `ARGO_USER` | no | `user` field Argo requires | `brettin` |

`GITHUB_TOKEN` is provided automatically by Actions and needs no setup.

## The self-hosted runner (lambda5)

Installed under `~/actions-runner` and managed as a user systemd service:

```bash
systemctl --user status gh-runner.service   # check
systemctl --user restart gh-runner.service  # restart
journalctl --user -u gh-runner.service -f   # logs
```

Linger is enabled for the account, so the runner starts on boot.

## Notes / limitations

- The agent cannot **approve your own PRs**: GitHub rejects a self-approval, so
  for PRs you author the review is posted as a comment instead. Reviews on other
  authors' PRs post as normal Approve / Request-changes.
- A bot's `REQUEST_CHANGES` does **not** satisfy a branch-protection rule that
  requires human approvals; it does visibly flag the PR and notify the author.
