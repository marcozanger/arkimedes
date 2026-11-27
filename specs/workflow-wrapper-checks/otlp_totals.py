#!/usr/bin/env python3
"""Sum cost and tokens of api_request events per session id and workflow run id (check C5)."""
import json
import os
from collections import defaultdict
from pathlib import Path

HOME = Path(os.environ.get("ARK_CHECKS_HOME", Path.home() / "ark-checks"))
OUT = HOME / "state" / "otlp.jsonl"
FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "cache_creation_tokens", "cost_usd")


def attributes(record):
    return {a["key"]: next(iter(a.get("value", {}).values()), None)
            for a in record.get("attributes", [])}


def number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def main():
    if not OUT.exists():
        raise SystemExit(f"no telemetry received: {OUT} is missing")
    totals = defaultdict(lambda: defaultdict(float))
    for line in OUT.read_text().splitlines():
        for resource in json.loads(line)["payload"].get("resourceLogs", []):
            for scope in resource.get("scopeLogs", []):
                for record in scope.get("logRecords", []):
                    attrs = attributes(record)
                    if attrs.get("event.name") not in ("api_request", "claude_code.api_request"):
                        continue
                    key = (attrs.get("session.id", "?"), attrs.get("workflow.run_id", "-"))
                    totals[key]["requests"] += 1
                    for field in FIELDS:
                        totals[key][field] += number(attrs.get(field))
    if not totals:
        raise SystemExit("telemetry arrived but holds no api_request events")
    for (session, run), t in sorted(totals.items()):
        print(f"session {session}  workflow {run}")
        print("  requests {:.0f}  in {:.0f}  out {:.0f}  cache read {:.0f}  cache write {:.0f}"
              "  usd {:.4f}".format(t["requests"], *(t[f] for f in FIELDS)))


if __name__ == "__main__":
    main()
