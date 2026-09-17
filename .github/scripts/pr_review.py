#!/usr/bin/env python3
"""PR Review Agent for brettin/SystemTests.

Runs on the self-hosted lambda5 runner (which can reach Argo). Gathers the PR
diff, the full repository source, the docs, and any existing PR discussion, asks
Argo for a structured verdict, and posts back a formal GitHub review:

  * APPROVE          -> code is good to merge
  * REQUEST_CHANGES  -> author must address the listed issues
  * COMMENT          -> fallback when the model won't commit to a verdict

Triggered on PR open/reopen/synchronize and on new PR comments, so it re-reviews
whenever code or the conversation changes.

Environment (all provided by the workflow):
  GITHUB_TOKEN / GH_TOKEN, GITHUB_REPOSITORY, PR_NUMBER
  ARGO_TOKEN   (required)  bearer token
  ARGO_URL     (optional)  default: native Argo chat endpoint
  ARGO_MODEL   (optional)  default: gpt4o
  ARGO_USER    (optional)  default: brettin  (Argo requires a 'user' field)
"""
from __future__ import annotations

import json
import os
import sys
from urllib.parse import urlparse, urlunparse

import requests

API = "https://api.github.com"
TIMEOUT = 300

# Files we never send to the model (binary / noise / the agent's own machinery).
SKIP_DIRS = {".git", ".github", ".review-venv", "__pycache__", ".venv", "node_modules"}
SKIP_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".zip", ".gz", ".tar", ".whl",
    ".so", ".bin", ".ico", ".pyc", ".lock",
}
MAX_FILE_BYTES = 60_000            # per-file cap sent to the model
MAX_TOTAL_SOURCE_BYTES = 350_000   # total repo-source budget


def env(name: str, default: str | None = None, required: bool = False) -> str:
    val = os.environ.get(name, default)
    if required and not val:
        sys.exit(f"ERROR: missing required env var {name}")
    return val or ""


REPO = env("GITHUB_REPOSITORY", required=True)
PR_NUMBER = env("PR_NUMBER", required=True)
GH_TOKEN = env("GITHUB_TOKEN") or env("GH_TOKEN", required=True)
ARGO_TOKEN = env("ARGO_TOKEN", required=True)
ARGO_URL = env("ARGO_URL", "https://apps.inside.anl.gov/argoapi/api/v1/resource/chat/")
ARGO_MODEL = env("ARGO_MODEL", "gpt4o")
ARGO_USER = env("ARGO_USER", "brettin")

GH_HEADERS = {
    "Authorization": f"Bearer {GH_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# --------------------------------------------------------------------------- #
# GitHub helpers
# --------------------------------------------------------------------------- #
def gh_get(path: str, **params):
    r = requests.get(f"{API}/{path}", headers=GH_HEADERS, params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def gh_paginate(path: str, **params):
    out, page = [], 1
    while True:
        batch = gh_get(path, per_page=100, page=page, **params)
        if not batch:
            break
        out.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return out


def get_pr():
    return gh_get(f"repos/{REPO}/pulls/{PR_NUMBER}")


def get_diff() -> str:
    h = dict(GH_HEADERS)
    h["Accept"] = "application/vnd.github.v3.diff"
    r = requests.get(f"{API}/repos/{REPO}/pulls/{PR_NUMBER}", headers=h, timeout=60)
    r.raise_for_status()
    return r.text


def get_discussion() -> str:
    """Existing issue comments + review comments + prior reviews, chronological-ish."""
    lines = []
    for c in gh_paginate(f"repos/{REPO}/issues/{PR_NUMBER}/comments"):
        lines.append(f"[comment by @{c['user']['login']}]\n{c['body']}")
    for c in gh_paginate(f"repos/{REPO}/pulls/{PR_NUMBER}/comments"):
        loc = f" ({c.get('path')}:{c.get('line') or c.get('original_line')})"
        lines.append(f"[inline review comment by @{c['user']['login']}{loc}]\n{c['body']}")
    for rv in gh_paginate(f"repos/{REPO}/pulls/{PR_NUMBER}/reviews"):
        if rv.get("body"):
            lines.append(f"[prior review by @{rv['user']['login']} -> {rv['state']}]\n{rv['body']}")
    return "\n\n".join(lines) if lines else "(no prior discussion)"


def collect_source() -> str:
    """Concatenate the repository's text files (post-checkout working tree)."""
    chunks, total = [], 0
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fn in sorted(files):
            path = os.path.join(root, fn)
            rel = os.path.relpath(path, ".")
            if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                continue
            try:
                if os.path.getsize(path) > MAX_FILE_BYTES:
                    continue
                with open(path, "r", encoding="utf-8", errors="replace") as fh:
                    body = fh.read()
            except (OSError, UnicodeError):
                continue
            if "\x00" in body:
                continue
            piece = f"===== FILE: {rel} =====\n{body}\n"
            if total + len(piece) > MAX_TOTAL_SOURCE_BYTES:
                chunks.append("... (source truncated: repository exceeds budget) ...")
                return "\n".join(chunks)
            chunks.append(piece)
            total += len(piece)
    return "\n".join(chunks) if chunks else "(no text source files found)"


# --------------------------------------------------------------------------- #
# Argo
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = (
    "You are pr-review-agent, a rigorous senior code reviewer for the GitHub "
    "repository brettin/SystemTests. You are given the full repository source, "
    "the documentation, the pull request diff, and the existing PR discussion. "
    "Review the pull request for correctness, security, error handling, clarity, "
    "consistency with the existing codebase and docs, and test coverage. If the "
    "PR was already reviewed, take the prior discussion and any new commits or "
    "comments into account and focus on what changed.\n\n"
    "Respond with STRICT JSON only (no markdown, no code fences), matching:\n"
    '{\n'
    '  "verdict": "APPROVE" | "REQUEST_CHANGES",\n'
    '  "summary": "<2-4 sentence overall assessment>",\n'
    '  "strengths": ["..."],\n'
    '  "issues": [{"severity":"high|medium|low","file":"path or null",'
    '"comment":"what is wrong and why"}],\n'
    '  "required_changes": ["concrete action the author must take", "..."]\n'
    '}\n'
    "Use APPROVE only when there are no high or medium severity issues. "
    "Otherwise use REQUEST_CHANGES and make required_changes specific and actionable."
)


def call_argo(user_content: str) -> str:
    parsed = urlparse(ARGO_URL)
    base = urlunparse(parsed).rstrip("/")
    openai_compatible = base.endswith("/v1")
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {ARGO_TOKEN}",
    }
    if openai_compatible:
        url = base + "/chat/completions"
        payload = {
            "model": ARGO_MODEL,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
        }
    else:
        url = base
        payload = {
            "user": ARGO_USER,
            "model": ARGO_MODEL,
            "system": SYSTEM_PROMPT,
            "prompt": [user_content],
        }
    r = requests.post(url, data=json.dumps(payload), headers=headers, timeout=TIMEOUT)
    if not r.ok:
        sys.exit(f"ERROR: Argo request failed [{r.status_code}]: {r.text[:500]}")
    data = r.json()
    # Native Argo returns {"response": "..."}; OpenAI-compatible returns choices[].
    if openai_compatible:
        return data["choices"][0]["message"]["content"]
    return data.get("response") or data.get("text") or json.dumps(data)


def extract_json(text: str) -> dict | None:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if 0 <= start < end:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                return None
    return None


# --------------------------------------------------------------------------- #
# Render + post
# --------------------------------------------------------------------------- #
def render_body(v: dict, model: str) -> str:
    icon = "✅" if v.get("verdict") == "APPROVE" else "🔧"
    out = [f"## {icon} PR Review Agent — {v.get('verdict', 'COMMENT')}", ""]
    if v.get("summary"):
        out += [v["summary"], ""]
    if v.get("strengths"):
        out += ["**Strengths**"] + [f"- {s}" for s in v["strengths"]] + [""]
    if v.get("issues"):
        out += ["**Issues**"]
        for it in v["issues"]:
            sev = str(it.get("severity", "")).upper()
            loc = f"`{it['file']}` — " if it.get("file") else ""
            out.append(f"- **[{sev}]** {loc}{it.get('comment', '')}")
        out.append("")
    if v.get("required_changes"):
        out += ["**Required changes**"] + [f"- [ ] {c}" for c in v["required_changes"]] + [""]
    out += ["---", f"<sub>Automated review via Argo (`{model}`). "
            "Re-runs on new commits and comments.</sub>"]
    return "\n".join(out)


def post_review(body: str, event: str):
    payload = {"body": body, "event": event}
    r = requests.post(
        f"{API}/repos/{REPO}/pulls/{PR_NUMBER}/reviews",
        headers=GH_HEADERS, data=json.dumps(payload), timeout=60,
    )
    if not r.ok:
        # Fallback: APPROVE on your own PR is rejected by GitHub; drop to a comment.
        print(f"WARN: review POST failed [{r.status_code}]: {r.text[:300]}", file=sys.stderr)
        rc = requests.post(
            f"{API}/repos/{REPO}/issues/{PR_NUMBER}/comments",
            headers=GH_HEADERS, data=json.dumps({"body": body}), timeout=60,
        )
        rc.raise_for_status()
        print("Posted as issue comment (review event was rejected).")
    else:
        print(f"Posted review: {event}")


def main():
    pr = get_pr()
    print(f"Reviewing PR #{PR_NUMBER}: {pr.get('title')!r} (model={ARGO_MODEL})")
    diff = get_diff()
    discussion = get_discussion()
    source = collect_source()

    user_content = (
        f"# Pull Request #{PR_NUMBER}: {pr.get('title')}\n\n"
        f"Author: @{pr['user']['login']}\n"
        f"Description:\n{pr.get('body') or '(no description)'}\n\n"
        f"## Existing discussion\n{discussion}\n\n"
        f"## Diff\n```diff\n{diff}\n```\n\n"
        f"## Full repository source (for context)\n{source}\n"
    )

    raw = call_argo(user_content)
    verdict = extract_json(raw)

    if not verdict or verdict.get("verdict") not in ("APPROVE", "REQUEST_CHANGES"):
        body = ("## ⚠️ PR Review Agent\n\nThe model did not return a parseable "
                "verdict. Raw response:\n\n" + raw[:5000])
        post_review(body, "COMMENT")
        return

    event = "APPROVE" if verdict["verdict"] == "APPROVE" else "REQUEST_CHANGES"
    post_review(render_body(verdict, ARGO_MODEL), event)


if __name__ == "__main__":
    main()
