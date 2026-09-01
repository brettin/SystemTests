import argparse
import json
from urllib.parse import urlparse, urlunparse

import requests

# Extra (non-required) chat-completion parameters to probe for support.
# Each is sent on its own so a single unsupported parameter can be identified.
PROBE_PARAMETERS = {
    "temperature": 0.1,
    "top_p": 0.9,
    "max_tokens": 16,
    "max_completion_tokens": 16,
    "stop": [],
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "n": 1,
    "seed": 42,
    "user": "brettin",
}

PROBE_MESSAGES = [
    {"role": "user", "content": "Say hi."},
]


def summarize_error(response):
    """Return a short, human-readable reason for a failed response."""
    try:
        body = response.json()
    except ValueError:
        return response.text.strip()[:200] or "<empty body>"

    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])[:200]
        if isinstance(error, str):
            return error[:200]
        if body.get("message"):
            return str(body["message"])[:200]
    return json.dumps(body)[:200]


def send_chat_request(chat_url, headers, model, extra_params=None, timeout=120):
    """POST a minimal chat-completion request, optionally with extra parameters."""
    body = {"model": model, "messages": PROBE_MESSAGES}
    if extra_params:
        body.update(extra_params)
    return requests.post(chat_url, data=json.dumps(body), headers=headers, timeout=timeout)


def probe_parameters(chat_url, headers, model, parameters=None):
    """Probe which extra chat-completion parameters a model accepts.

    Sends a baseline request first, then one request per parameter, and finally a
    combined request using every parameter the model accepted individually.
    Returns a dict mapping parameter name -> bool (accepted).
    """
    parameters = PROBE_PARAMETERS if parameters is None else parameters

    print(f"\nProbing extra parameters for model: {model}")
    print("-" * 60)

    baseline = send_chat_request(chat_url, headers, model)
    if not baseline.ok:
        print(f"  baseline (no extra params): FAILED [{baseline.status_code}]")
        print(f"    reason: {summarize_error(baseline)}")
        print("  Skipping parameter probes because the baseline request failed.")
        return {}
    print("  baseline (no extra params): OK [200]")

    results = {}
    for name, value in parameters.items():
        response = send_chat_request(chat_url, headers, model, {name: value})
        accepted = response.ok
        results[name] = accepted
        status = "accepted" if accepted else "rejected"
        print(f"  {name:<24} {status} [{response.status_code}]")
        if not accepted:
            print(f"    reason: {summarize_error(response)}")

    accepted_params = {name: parameters[name] for name, ok in results.items() if ok}
    if accepted_params:
        combined = send_chat_request(chat_url, headers, model, accepted_params)
        status = "accepted" if combined.ok else "rejected"
        print(f"\n  all accepted params together: {status} [{combined.status_code}]")
        if not combined.ok:
            print(f"    reason: {summarize_error(combined)}")

    supported = sorted(name for name, ok in results.items() if ok)
    unsupported = sorted(name for name, ok in results.items() if not ok)
    print(f"\n  Supported:   {', '.join(supported) if supported else '(none)'}")
    print(f"  Unsupported: {', '.join(unsupported) if unsupported else '(none)'}")

    return results


parser = argparse.ArgumentParser(description="Send a test request to the Argo chat API.")
parser.add_argument(
    "--url",
    default="https://apps.inside.anl.gov/argoapi/api/v1/resource/chat/",
    help="Argo chat API endpoint",
)
parser.add_argument("--token", required=True, help="Bearer token for authentication")
parser.add_argument("--model", default="gpt4o", help="Model name")
parser.add_argument(
    "--probe",
    action="store_true",
    help="Probe which extra chat-completion parameters the model accepts, then exit",
)
parser.add_argument(
    "--probe-params",
    help="Comma-separated subset of parameters to probe (default: all known parameters)",
)
args = parser.parse_args()

parsed_url = urlparse(args.url)
if parsed_url.hostname in {"localhost", "127.0.0.1"} and parsed_url.scheme == "https":
    parsed_url = parsed_url._replace(scheme="http")
base_url = urlunparse(parsed_url).rstrip("/")
openai_compatible = base_url.endswith("/v1")
url = base_url
if openai_compatible:
    url += "/chat/completions"
    data = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": "You are a large language model with the name Argo."},
            {"role": "user", "content": "Can you write a function to calculate pi?"},
        ],
    }
else:
    data = {
        "user": "brettin",
        "model": args.model,
        "system": "You are a large language model with the name Argo.",
        "prompt": ["Can you write a function to calculate pi?"],
    }

# Convert the dict to JSON
payload = json.dumps(data)

# Add headers for JSON content and bearer-token authentication
headers = {
    "Content-Type": "application/json",
    "Authorization": f"Bearer {args.token}",
}

if openai_compatible:
    models_response = requests.get(f"{base_url}/models", headers=headers)
    if not models_response.ok:
        print("Request Headers:", headers)
    models_response.raise_for_status()
    models = models_response.json().get("data", [])
    print("Available Models:")
    for model in models:
        print(f"- {model['id']}")

if args.probe:
    if not openai_compatible:
        parser.error("--probe requires an OpenAI-compatible endpoint (URL ending in /v1)")
    if args.probe_params:
        requested = [name.strip() for name in args.probe_params.split(",") if name.strip()]
        unknown = [name for name in requested if name not in PROBE_PARAMETERS]
        if unknown:
            parser.error(
                f"Unknown probe parameter(s): {', '.join(unknown)}. "
                f"Known parameters: {', '.join(sorted(PROBE_PARAMETERS))}"
            )
        selected = {name: PROBE_PARAMETERS[name] for name in requested}
    else:
        selected = PROBE_PARAMETERS
    probe_parameters(url, headers, args.model, selected)
    raise SystemExit(0)

# Send POST request
response = requests.post(url, data=payload, headers=headers)

# Receive the response data
print("Status Code:", response.status_code)
if not response.ok:
    print("Request Headers:", headers)
print("JSON Response ", response.json())
