#!/usr/bin/env bash
# One German triage request through the tunnel, with the shape the BYOK backend uses:
# OpenAI-compatible chat completion at http://127.0.0.1:11435/v1, a per-request
# session header, max_tokens=300 and a JSON-schema response format.
#
# Usage: ./smoke.sh [model] [--json-out PATH]
#
# Run it where the tunnel from tunnel.sh is listening. Prints prompt/completion
# tokens, wall time and tokens per second. Exit codes: 0 valid answer, 1 invalid
# or failed request, 2 usage problem.

set -euo pipefail

MODEL="${1:-qwen2.5:1.5b}"
BASE="${AIPF_OLLAMA_BASE:-http://127.0.0.1:11435/v1}"
OUT=""
if [ "${2:-}" = "--json-out" ] && [ -n "${3:-}" ]; then
    OUT="$3"
fi

command -v curl >/dev/null 2>&1 || { echo "smoke: curl is required" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "smoke: python3 is required" >&2; exit 2; }

session="$(python3 -c 'import uuid; print(uuid.uuid4())')"
tmp="$(mktemp)"
trap 'rm -f "${tmp}" "${tmp}.body"' EXIT

# Synthetic enquiry, already pseudonymised: placeholders only, no real person.
read -r -d '' PROMPT <<'PROMPT' || true
Du bist ein Triage-Assistent für ein kleines Unternehmen. Ordne die folgende
Anfrage zu und antworte NUR mit JSON.

Kategorien: QUOTE_REQUEST, SUPPORT, JOB_APPLICATION, PARTNERSHIP, OTHER.
Dringlichkeit: LOW, NORMAL, HIGH.
reply_draft: eine kurze, höfliche deutsche Antwort (maximal 3 Sätze), die die
Platzhalter unverändert übernimmt. Keine erfundenen Zusagen.

Anfrage:
"Guten Tag, wir benötigen ein Angebot für die Reinigung unserer Büroräume in
[POSTCODE_CITY_1]. Bitte senden Sie die Konditionen an [PERSON_1] per E-Mail an
[EMAIL_1]. Ein Termin diese Woche wäre gut."
PROMPT

SCHEMA='{
  "type": "object",
  "properties": {
    "category": {"type": "string", "enum": ["QUOTE_REQUEST", "SUPPORT", "JOB_APPLICATION", "PARTNERSHIP", "OTHER"]},
    "urgency": {"type": "string", "enum": ["LOW", "NORMAL", "HIGH"]},
    "reply_draft": {"type": "string"}
  },
  "required": ["category", "urgency", "reply_draft"],
  "additionalProperties": false
}'

python3 - "${tmp}" "${MODEL}" "${PROMPT}" "${SCHEMA}" <<'PY'
import json
import sys

path, model, prompt, schema = sys.argv[1:5]
body = {
    "model": model,
    "messages": [
        {"role": "system", "content": "Antworte ausschließlich mit gültigem JSON."},
        {"role": "user", "content": prompt},
    ],
    "max_tokens": 300,
    "temperature": 0,
    "stream": False,
    "response_format": {
        "type": "json_schema",
        "json_schema": {"name": "triage", "strict": True, "schema": json.loads(schema)},
    },
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(body, handle)
PY

request() {
    curl -sS --max-time 180 -o "${tmp}.body" -w '%{http_code} %{time_total}' \
        -H 'Content-Type: application/json' \
        -H 'User-Agent: ai-privacy-filter/ops-smoke' \
        -H "x-opencode-session: ${session}" \
        --data @"${tmp}" \
        "${BASE}/chat/completions"
}

read -r status elapsed <<<"$(request)"
used_schema="json_schema"

# Some builds accept json_schema but answer prose; fall back to json_object and
# say so in the result rather than pretending the first shape worked.
if [ "${status}" != "200" ] || ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))["choices"][0]["message"]["content"]' "${tmp}.body" 2>/dev/null; then
    python3 - "${tmp}" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as handle:
    body = json.load(handle)
body.pop("response_format", None)
body["response_format"] = {"type": "json_object"}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(body, handle)
PY
    read -r status elapsed <<<"$(request)"
    used_schema="json_object"
fi

[ "${status}" = "200" ] || {
    echo "smoke: HTTP ${status}" >&2
    head -c 400 "${tmp}.body" >&2
    echo >&2
    exit 1
}

RESPONSE_PATH="${tmp}.body" MODEL="${MODEL}" ELAPSED="${elapsed}" USED_SCHEMA="${used_schema}" \
    OUT="${OUT}" python3 <<'PY'
import json
import os
import sys

with open(os.environ["RESPONSE_PATH"], encoding="utf-8") as handle:
    response = json.load(handle)
try:
    content = response["choices"][0]["message"]["content"]
    parsed = json.loads(content)
except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
    print(f"smoke: response is not JSON content: {exc}", file=sys.stderr)
    sys.exit(1)

problems = []
if parsed.get("category") not in {"QUOTE_REQUEST", "SUPPORT", "JOB_APPLICATION", "PARTNERSHIP", "OTHER"}:
    problems.append(f"category={parsed.get('category')!r}")
if parsed.get("urgency") not in {"LOW", "NORMAL", "HIGH"}:
    problems.append(f"urgency={parsed.get('urgency')!r}")
if not isinstance(parsed.get("reply_draft"), str) or not parsed["reply_draft"].strip():
    problems.append("reply_draft missing")
if problems:
    print("smoke: schema-invalid answer: " + ", ".join(problems), file=sys.stderr)
    sys.exit(1)

usage = response.get("usage") or {}
elapsed = float(os.environ["ELAPSED"])
completion = int(usage.get("completion_tokens") or 0)
tps = round(completion / elapsed, 2) if elapsed > 0 and completion else None
result = {
    "model": response.get("model") or os.environ["MODEL"],
    "response_format": os.environ["USED_SCHEMA"],
    "elapsed_s": round(elapsed, 2),
    "usage": usage,
    "tokens_per_s": tps,
    "category": parsed["category"],
    "urgency": parsed["urgency"],
    "reply_draft": parsed["reply_draft"],
}
print(json.dumps(result, ensure_ascii=False, indent=2))
if os.environ["OUT"]:
    with open(os.environ["OUT"], "w", encoding="utf-8") as handle:
        json.dump(result, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
PY
