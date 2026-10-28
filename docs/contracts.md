# Contracts

These models are defined once in `src/ai_privacy_filter/models.py` and used by
every later task. They are frozen pydantic v2 models with `extra="forbid"`.

## Entities and spans

`EntityType` is a string enum with exactly these members:

`PERSON`, `ORG`, `EMAIL`, `PHONE`, `IBAN`, `ADDRESS_STREET`, `POSTCODE_CITY`,
`URL`, `VAT_ID`, `DATE_OF_BIRTH`.

`Span`:

| field | type | notes |
|---|---|---|
| `start` | `int` | inclusive character offset, `>= 0` |
| `end` | `int` | exclusive character offset, `>= start` |
| `type` | `EntityType` | |
| `text` | `str` | expected to equal `source_text[start:end]` |
| `score` | `float` | `0.0 .. 1.0` |
| `source` | `str` | recognizer or backend that produced the span |

The model cannot check `text` against the source because it never receives the
source. Detection tests assert `source_text[span.start : span.end] == span.text`.

## Redaction and restore

`RedactionResult`:

| field | type | notes |
|---|---|---|
| `redacted_text` | `str` | text with placeholders |
| `mapping` | `dict[str, str]` | placeholder to original, **in memory only** |
| `spans` | `list[Span]` | spans that were replaced |

`mapping` uses `repr=False`, so printing the model does not print personal data.
It must never be logged, persisted or put in a response body.

`pseudonymise.redact` numbers placeholders `[TYPE_n]` per type, in order of first
appearance, and reuses one placeholder for equal values. Equality is NFC,
case-folded and whitespace-collapsed, so `Max  Müller` and `MAX MÜLLER` share
`[PERSON_1]`. When spans overlap, the earlier, longer span wins; empty,
whitespace-only and out-of-range spans are ignored. The mapping keeps the first
written form of a value.

`pseudonymise.restore` accepts placeholders whose case, inner spacing or
zero-padding changed (`[ person _ 01 ]` restores as `[PERSON_1]`). Placeholders
with no mapping entry are left exactly as written and listed once each in
`unknown_placeholders`.

`RestoreResult`:

| field | type | notes |
|---|---|---|
| `text` | `str` | draft with originals put back |
| `unknown_placeholders` | `list[str]` | placeholders with no mapping entry, left as written |

## Guard

`GuardVerdict`:

| field | type | notes |
|---|---|---|
| `ok` | `bool` | `True` only when no original value occurs in the outbound text |
| `leaked_types` | `list[EntityType]` | types only, never values |

Validation rejects `ok=True` together with a non-empty `leaked_types`. On
`ok=False` the caller aborts before any LLM call.

`guard.guard` compares every mapped original in its normalised form (case-folded,
whitespace-collapsed) against the outbound text. For `PHONE` and `IBAN` it also
compares the ASCII digits only, with at least four digits, so reformatting does
not hide a leak. `guard.require_clean` raises `guard.GuardBlockedError` instead of
returning, so a pipeline cannot call the model by accident.

## Triage

`Category`: `QUOTE_REQUEST`, `SUPPORT`, `JOB_APPLICATION`, `PARTNERSHIP`, `OTHER`.

`Urgency`: `LOW`, `NORMAL`, `HIGH`.

`TriageResult`:

| field | type | notes |
|---|---|---|
| `category` | `Category` | |
| `urgency` | `Urgency` | |
| `reply_draft` | `str` | written against placeholders by the model |
| `backend` | `str` | backend name, e.g. `fake` |
| `model` | `str \| None` | provider model id when known |
| `latency_ms` | `int` | measured wall-clock time, `>= 0` |

`triage.TRIAGE_SCHEMA` is the JSON schema every backend answers against:
required `category`, `urgency`, `reply_draft`, no additional properties.

## Backends

`LLMBackend` (`src/ai_privacy_filter/backends/base.py`) is a runtime-checkable
protocol:

```python
class LLMBackend(Protocol):
    name: str

    def is_available(self) -> bool: ...

    def complete_json(
        self, prompt: str, schema: dict[str, Any], timeout_s: int = 30
    ) -> BackendResult: ...
```

`BackendResult`:

| field | type | notes |
|---|---|---|
| `text` | `str` | raw model text |
| `parsed` | `dict \| None` | validated JSON object on success |
| `model` | `str \| None` | provider model id |
| `latency_ms` | `int` | `>= 0` |
| `usage` | `dict \| None` | provider token usage, including reasoning tokens when reported |
| `error_kind` | `str \| None` | `None` on success; otherwise the failure kind |

Provider failures are returned, not raised: a backend failure must not abort the
caller with an exception. `error_kind` is drawn from a fixed vocabulary
(`not_installed`, `not_configured`, `timeout`, `auth_error`, `billing_error`,
`quota_exhausted`, `rate_limited`, `tool_use`, `cli_error`, `http_error`,
`network_error`, `empty_response`, `invalid_response`, `invalid_json`,
`schema_error`); `docs/backends.md` defines each one, the exact CLI argv, the
hermetic environment and the quarantine model (fresh empty temp directory, stdin
prompt, prompt never in argv).

### Fake backend

`FakeBackend` is fully implemented. It reads the SHA-256 digest of the
prompt, picks a category and urgency from it, fills a German template with the
first placeholder found in the prompt (or `Ihnen`), and returns JSON matching
`TRIAGE_SCHEMA`. The same prompt always yields the same result, including
latency. It makes no network call and needs no model download.

## Enquiries

`Enquiry`:

| field | type | notes |
|---|---|---|
| `id` | `str` | non-empty |
| `split` | `"dev" \| "test"` | |
| `style` | `str` | e.g. `formal_letter`, `web_form` |
| `text` | `str` | synthetic only |
| `gold_spans` | `list[Span]` | character-exact |
| `gold_category` | `Category` | |
| `gold_urgency` | `Urgency` | |

## Page data

Written by `scripts/page_export.py` in a later task and read by the site page.

`examples.json` is an array of `PageExample`:

| field | type |
|---|---|
| `id` | `str` |
| `original` | `str` |
| `redacted` | `str` |
| `reply_draft_redacted` | `str` |
| `reply_draft_restored` | `str` |
| `spans` | `list[Span]` |
| `misses` | `list[Span]` |
| `category` | `Category` |
| `urgency` | `Urgency` |
| `backend` | `str` |
| `mock` | `bool \| None` (`true` for mock page data, `false` for real results) |

Shape:

```json
[
  {
    "id": "example-001",
    "original": "Guten Tag, mein Name ist [PERSON_1] ...",
    "redacted": "Guten Tag, mein Name ist [PERSON_1] ...",
    "reply_draft_redacted": "Guten Tag [PERSON_1], ...",
    "reply_draft_restored": "Guten Tag [PERSON_1], ...",
    "spans": [],
    "misses": [],
    "category": "QUOTE_REQUEST",
    "urgency": "NORMAL",
    "backend": "fake",
    "mock": true
  }
]
```

`results.json` is `PageResults`:

| field | type |
|---|---|
| `detection.germeval` | `dict` |
| `detection.synthetic` | `dict` |
| `triage.per_backend` | `dict` |
| `meta.version` | `str` |
| `meta.commit` | `str` |
| `meta.generated_at` | ISO-8601 datetime |
| `meta.sha256` | `str` |
| `meta.mock` | `bool \| None` |

Shape:

```json
{
  "detection": {"germeval": {}, "synthetic": {}},
  "triage": {"per_backend": {}},
  "meta": {
    "version": "0.1.0",
    "commit": "0000000",
    "generated_at": "2026-10-27T00:00:00Z",
    "sha256": "0",
    "mock": true
  }
}
```

## Detection

`detect.detect(text: str, *, profile: "strict" | "balanced" = "balanced") -> list[Span]`
runs the Presidio `AnalyzerEngine` with the German spaCy model
(`de_core_news_lg`, language `de`) and the recognizers in
`ai_privacy_filter/recognizers/`. It makes no network call.

Returned spans are sorted by `(start, end)` and never overlap. Overlaps are
resolved deterministically: higher `score`, then longer span, then entity
priority (email, IBAN, URL, VAT ID, phone, street, postcode/city, date of birth,
person, organisation), then start, end and source. `text[start:end]` equals
`span.text` for every returned span.

`profile="balanced"` is the default and avoids over-masking on ambiguous
evidence. `profile="strict"` favours recall: lower score threshold, shorter phone
numbers accepted, IBAN groups may use hyphens or dots, and a lone German first
name from the bundled gazetteer is reported.

A `PERSON` span covers the whole mention the way the synthetic corpus annotates
it: an optional salutation (`Herr`, `Frau`), academic titles (`Dr.`, `Prof.`,
`Dipl.-Ing.`, `Univ.Prof.`, `Ing.`, `Mag.`), the name and post-nominal titles
(`B.Sc.`, `M.Eng.`, `MBA.`, `LL.M.`). A title without a name is never a span.

## Module status

| module | state |
|---|---|
| `models.py` | complete |
| `backends/fake.py` | complete |
| `backends/registry.py` | complete: registered backends, no probing |
| `backends/byok.py`, `claude_cli.py`, `codex_cli.py` | complete |
| `backends/env.py`, `isolation.py`, `schema.py`, `quota.py` | complete |
| `detect.py`, `recognizers/` | complete |
| `pseudonymise.py`, `guard.py` | complete |
| `triage.py` | complete |
| `api.py`, `tickets.py`, `pipeline.py` | complete |
| `cli.py` | complete for `redact`, `process`, `eval`, `serve` |

## Wording rules

Documentation and UI say *pseudonymises*, *runs entirely on your machine* and
*not legal advice*. Numbers are reported as measured, including misses and failed
backends.
