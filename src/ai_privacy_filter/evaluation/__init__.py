"""Evaluation helpers for the triage stage.

The modules here implement the parts of ``METHOD.md`` that belong to T12:
the fixed 40-enquiry subset, the per-backend run and its stored responses, the
triage metrics, the deterministic rebuild and the page export.

Nothing here logs or persists an original personal value. Responses are stored
with placeholders only; a reply that contains a gold value is discarded and
recorded as a leak by type.
"""
