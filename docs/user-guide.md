# User Guide

A practical guide to using the Argo-backed model endpoint hosted on `lambda5`.

> Last verified end to end on **2026-08-31**: SSH tunnel + Tailscale Funnel up,
> `/v1/models` returned 51 models, and `argo:gpt-4o` answered a chat request
> with `Status Code: 200`.

## Contents

1. [Setting up Cursor to use the lambda5 endpoint](#1-setting-up-cursor-to-use-the-lambda5-endpoint)
2. [Cheatsheet](#2-cheatsheet)

---

## 1. Setting up Cursor to use the lambda5 endpoint

This section walks through pointing Cursor at the OpenAI-compatible model server
running on `lambda5`, including the network plumbing needed to reach it from
outside the firewall.

### 1.1 Overview

The model server listens on `lambda5:44497` and is **behind a firewall**, so
Cursor cannot reach it directly. The working setup chains three hops:

```
Cursor  ──►  Tailscale (public HTTPS URL)  ──►  your laptop  ──►  lambda5:44497
```

Your laptop acts as the gateway: an SSH tunnel brings the server's port local,
and Tailscale publishes that local port at a stable HTTPS address that Cursor
can reach.

### 1.2 Prerequisites


| Requirement                       | Notes                                                  |
| --------------------------------- | ------------------------------------------------------ |
| SSH access to `lambda5`           | Directly or through a bastion/jump host                |
| Tailscale installed and logged in | On the laptop acting as the gateway                    |
| Tailscale Funnel enabled          | Needed for a public HTTPS URL; check your tailnet ACLs |
| Python with `requests`            | For running `test_argo.py` verification                |
| API token                         | The bearer token accepted by the endpoint              |


### 1.3 Step 1 — Forward the port from lambda5

Open an SSH tunnel so `lambda5:44497` becomes reachable at `localhost:44497` on
your laptop:

```bash
ssh -N -L 44497:localhost:44497 user@lambda5
```

If you must go through a jump host:

```bash
ssh -N -L 44497:localhost:44497 -J jump@bastion user@lambda5
```

Leave this running. For a long-lived tunnel, use `autossh` or run it as a
background service so a dropped connection reconnects automatically.

Verify the tunnel locally:

```bash
curl -s http://localhost:44497/v1/models -H "Authorization: Bearer YOUR_TOKEN"
```

You should get JSON containing a `data` array of model IDs.

### 1.4 Step 2 — Publish the port with Tailscale

Cursor needs a reachable HTTPS URL. Use **Tailscale Funnel** for a public
address:

```bash
tailscale funnel --bg 44497
```

This produces a URL of the form:

```
https://<machine-name>.<tailnet>.ts.net
```

For example: `https://thomass-macbook-pro-2.tail652c0f.ts.net`

Check the state of what you have published:

```bash
tailscale funnel status
tailscale serve status
```

> **Tailnet-only alternative:** if only machines on your tailnet need access,
> use `tailscale serve --bg --tcp 44497 localhost:44497` instead and address the
> service at your laptop's Tailscale IP. Cursor must be running on a tailnet
> machine for this to work.

### 1.5 Step 3 — Verify the endpoint before configuring Cursor

Do **not** configure Cursor until a plain chat request succeeds. Use
`test_argo.py` from this repo:

```bash
python ./test_argo.py \
  --token YOUR_TOKEN \
  --model argo:gpt-4o \
  --url https://<machine-name>.<tailnet>.ts.net/v1
```

A healthy run prints the available model list followed by:

```
Status Code: 200
JSON Response  {'id': 'chatcmpl-...', 'choices': [{'message': {'role': 'assistant', ...
```

The `model` field in the response reports the concrete upstream build (for
example `argo:gpt-4o` resolved to `gpt-4o-2024-11-20`), which is useful when you
need to know exactly what answered.

If you see a non-200 status, resolve it before continuing — see
[1.8 Troubleshooting](#18-troubleshooting).

### 1.6 Step 4 — Configure Cursor

1. Open **Cursor Settings** (`Cmd+Shift+J` on macOS, `Ctrl+Shift+J` elsewhere).
2. Go to the **Models** section.
3. Enable **OpenAI API Key** and paste your token.
4. Enable **Override OpenAI Base URL** and enter the endpoint:
  ```
   https://<machine-name>.<tailnet>.ts.net/v1
  ```
5. Click **+ Add Model** and enter a model ID **exactly** as it appears in the
  `/v1/models` output, for example `argo:gpt-4o`.
6. Click **Verify** if the option is present.
7. Open Chat, select the model you added, and send a short prompt to confirm.

#### Configuration reference


| Setting                  | Value                                          |
| ------------------------ | ---------------------------------------------- |
| Override OpenAI Base URL | `https://<machine-name>.<tailnet>.ts.net/v1`   |
| OpenAI API Key           | Your bearer token                              |
| Model                    | Exact ID from `/v1/models`, e.g. `argo:gpt-4o` |


> **Important:** the base URL must end in `/v1` and must **not** include
> `/chat/completions` — Cursor appends that path itself.

### 1.7 Model names

Model IDs must match the server exactly. Names are namespaced with an `argo:`
prefix, and shorthand will fail. Get the current list with:

```bash
curl -s https://<machine-name>.<tailnet>.ts.net/v1/models \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Or simply run `test_argo.py`, which prints the list on every invocation.


| Correct                | Incorrect        |
| ---------------------- | ---------------- |
| `argo:gpt-4o`          | `gpt4o`          |
| `argo:claude-opus-4.8` | `claudeopus48`   |
| `argo:gemini-2.5-pro`  | `gemini-2.5-pro` |

#### Currently available models

Retrieved from `/v1/models` on **2026-08-31**. The catalog changes as models are
added and retired, so treat this as a snapshot and re-check `/v1/models` rather
than copying from here indefinitely.

**OpenAI**

```
argo:gpt-4o
argo:gpt-4.1          argo:gpt-4.1-mini      argo:gpt-4.1-nano
argo:gpt-5            argo:gpt-5-mini        argo:gpt-5-nano
argo:gpt-5.1          argo:gpt-5.2
argo:gpt-5.4          argo:gpt-5.4-mini      argo:gpt-5.4-nano
argo:gpt-5.5
argo:gpt-5.6-sol      argo:gpt-5.6-luna      argo:gpt-5.6-terra
```

Reasoning models (o-series) are exposed under two aliases each — the bare name
and a `gpt-` prefixed form. Both resolve to the same model:

```
argo:o1        / argo:gpt-o1
argo:o3        / argo:gpt-o3
argo:o3-mini   / argo:gpt-o3-mini
argo:o4-mini   / argo:gpt-o4-mini
```

**Anthropic**

Also dual-aliased, with the tier before or after the version:

```
argo:claude-opus-5      / argo:claude-5-opus
argo:claude-opus-4.8    / argo:claude-4.8-opus
argo:claude-opus-4.7    / argo:claude-4.7-opus
argo:claude-opus-4.6    / argo:claude-4.6-opus
argo:claude-opus-4.5    / argo:claude-4.5-opus
argo:claude-opus-4.1    / argo:claude-4.1-opus
argo:claude-sonnet-5    / argo:claude-5-sonnet
argo:claude-sonnet-4.6  / argo:claude-4.6-sonnet
argo:claude-sonnet-4.5  / argo:claude-4.5-sonnet
argo:claude-haiku-4.5   / argo:claude-4.5-haiku
```

**Google**

```
argo:gemini-2.5-pro    argo:gemini-2.5-flash
argo:gemini-3.5-flash  argo:gemini-3.1-flash-lite
```

**Embeddings** (not usable as Cursor chat models)

```
argo:text-embedding-ada-002
argo:text-embedding-3-small
argo:text-embedding-3-large
```

> Listing in `/v1/models` only means the gateway knows the name. It does not
> guarantee the model will work under Cursor — see
> [1.8 Troubleshooting](#18-troubleshooting) and run `--probe` before committing
> to a model.


### 1.8 Troubleshooting

#### Request fails with a 502 and an upstream parse error

```
Failed to parse upstream response: ... Value at 'choices[0].message' does not
match any variant of SystemMessage | UserMessage | AssistantMessage | ToolMessage
```

The gateway received something it could not convert to OpenAI format. Two
common causes:

1. **Unknown model name** — verify the ID against `/v1/models`.
2. **Unsupported request parameters** — some models (notably Claude and
  reasoning models) reject sampling parameters such as `temperature` and
   `top_p`.

Use the built-in probe to identify which parameters a model tolerates:

```bash
python ./test_argo.py \
  --token YOUR_TOKEN \
  --model argo:claude-opus-4.8 \
  --url https://<machine-name>.<tailnet>.ts.net/v1 \
  --probe
```

The probe sends a baseline request, then one request per parameter, and reports
which were accepted or rejected. To narrow the check:

```bash
python ./test_argo.py ... --probe --probe-params temperature,top_p
```

Because Cursor sends its own sampling parameters, a model that only works
without them may fail in Cursor even though `test_argo.py` succeeds with a
minimal payload. Prefer models that pass the probe with parameters enabled.

#### Cursor cannot connect at all

- Confirm the SSH tunnel and `tailscale funnel` are both still running.
- Try **Settings → Network → HTTP Compatibility Mode → HTTP/1.1**; some proxies
do not handle HTTP/2.
- Re-run `test_argo.py` against the same URL to isolate Cursor from the network.

#### Authentication errors

`test_argo.py` prints the request headers whenever a response is not successful,
which makes it easy to confirm the `Authorization` header is being sent as
expected.

#### Slow models time out

Cursor enforces a request timeout that is not user-configurable. Large models
served over the tunnel may exceed it even when they eventually respond.

### 1.9 Known limitations


| Area           | Limitation                                                              |
| -------------- | ----------------------------------------------------------------------- |
| Tab completion | Always uses Cursor's built-in models, never the custom endpoint         |
| Model coverage | Only models that pass a plain chat request will work                    |
| Availability   | If the SSH tunnel or Tailscale Funnel stops, Cursor loses access        |
| Parameters     | Models rejecting `temperature`/`top_p` may fail under Cursor's defaults |


### 1.10 Quick checklist

- [ ] SSH tunnel to `lambda5:44497` is running
- [ ] `tailscale funnel` is publishing port 44497
- [ ] `curl .../v1/models` returns the model list
- [ ] `test_argo.py` returns `Status Code: 200` for the chosen model
- [ ] Cursor base URL ends in `/v1` with no `/chat/completions`
- [ ] Model ID in Cursor matches `/v1/models` exactly

---

## 2. Cheatsheet

Copy-paste reference for bringing the network setup up, checking it, and tearing
it down. Replace placeholders before running:


| Placeholder                               | Example                                           |
| ----------------------------------------- | ------------------------------------------------- |
| `YOUR_TOKEN`                              | `YOUR_TOKEN`                                      |
| `user@lambda5`                            | your SSH login on lambda5                         |
| `https://<machine-name>.<tailnet>.ts.net` | `https://thomass-macbook-pro-2.tail652c0f.ts.net` |


### Start up

```bash
# 1. SSH tunnel — lambda5:44497 → localhost:44497 (foreground; Ctrl+C to stop)
ssh -N -L 44497:localhost:44497 user@lambda5

# With a jump host
ssh -N -L 44497:localhost:44497 -J jump@bastion user@lambda5

# SSH tunnel in the background
ssh -fN -L 44497:localhost:44497 user@lambda5

# 2. Publish port via Tailscale Funnel (public HTTPS)
tailscale funnel --bg 44497

# Alternative: tailnet-only (no public internet)
tailscale serve --bg --tcp 44497 localhost:44497
```

### Verify

```bash
# Tunnel — local check
curl -s http://localhost:44497/v1/models -H "Authorization: Bearer YOUR_TOKEN" | jq .

# Funnel — remote check
curl -s https://<machine-name>.<tailnet>.ts.net/v1/models \
  -H "Authorization: Bearer YOUR_TOKEN" | jq .

# Full chat test
python ./test_argo.py \
  --token YOUR_TOKEN \
  --model argo:gpt-4o \
  --url https://<machine-name>.<tailnet>.ts.net/v1

# Probe which extra parameters a model accepts
python ./test_argo.py \
  --token YOUR_TOKEN \
  --model argo:claude-opus-4.8 \
  --url https://<machine-name>.<tailnet>.ts.net/v1 \
  --probe

# Probe a subset of parameters only
python ./test_argo.py \
  --token YOUR_TOKEN \
  --model argo:claude-opus-4.8 \
  --url https://<machine-name>.<tailnet>.ts.net/v1 \
  --probe --probe-params temperature,top_p
```

### Status

```bash
# What Tailscale is publishing
tailscale funnel status
tailscale serve status

# Is the SSH tunnel listening on 44497?
lsof -iTCP:44497 -sTCP:LISTEN

# Find SSH tunnel process(es)
pgrep -fl "ssh.*44497:localhost:44497"
```

### Shut down

Stop in reverse order: Tailscale first, then the SSH tunnel.

```bash
# 1. Stop Tailscale Funnel (public HTTPS)

# Stop only port 44497 (matches: tailscale funnel --bg 44497)
tailscale funnel 44497 off

# Or clear all funnel configuration on this machine
tailscale funnel reset

# If you used tailnet-only serve instead
tailscale serve 44497 off
tailscale serve reset

# Confirm nothing is still published
tailscale funnel status
tailscale serve status

# 2. Stop the SSH tunnel

# If running in the foreground: Ctrl+C in that terminal

# If running in the background — find and kill by PID
pgrep -fl "ssh.*44497:localhost:44497"
kill <PID>

# Or kill every matching tunnel process
pkill -f "ssh -[fN]* -L 44497:localhost:44497"

# Confirm port 44497 is no longer listening
lsof -iTCP:44497 -sTCP:LISTEN
```

> **Note:** `tailscale funnel reset` and `tailscale serve reset` remove **all**
> funnel/serve configuration for this machine. Prefer `tailscale funnel 44497 off`
> (or `tailscale serve 44497 off`) when you only want to stop this endpoint.

### Cursor settings (reference)


| Setting                  | Value                                        |
| ------------------------ | -------------------------------------------- |
| Override OpenAI Base URL | `https://<machine-name>.<tailnet>.ts.net/v1` |
| OpenAI API Key           | `YOUR_TOKEN`                                 |
| Model                    | e.g. `argo:gpt-4o`                           |


