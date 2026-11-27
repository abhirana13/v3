"""Post a Block Kit message to a Slack Incoming Webhook. Stdlib only. Never prints the URL."""
from __future__ import annotations

import json
import urllib.error
import urllib.request


def post(url: str, payload: dict, timeout: int = 30) -> str:
    if not url.startswith("https://hooks.slack.com/"):
        return "SLACK_SKIPPED=not configured"
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            status = resp.status
            text = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        raise RuntimeError("slack post failed (%s): %s" % (e.code, e.read()[:200].decode("utf-8", "replace")))
    if status != 200:
        raise RuntimeError("slack post failed (%s): %s" % (status, text[:200]))
    return "SLACK_POSTED=ok"
