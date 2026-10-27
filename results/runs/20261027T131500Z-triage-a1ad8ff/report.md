# Triage report

Run: `20261027T131500Z-triage-a1ad8ff`  
Stage: `triage`  
Method SHA-256: `f76b5b8ada0f3f31666aae7abc00d6fb7d845a0ba3f4056c41c6a6ade67d2f86`  
Commit: `d994a11b88ab1393b33c420fb92beee5fc56835a`

The fixed 40-enquiry subset from METHOD.md section 8.
Accuracy uses 40 as the denominator for every available backend.
A failure, guard block or leak counts as an incorrect answer and is
reported separately.

## Backend quality

- **byok**: joint 27/40 = 67.5% (95% CI 52.0-79.9%); category 32/40 = 80.0% (95% CI 65.2-89.5%); urgency 35/40 = 87.5% (95% CI 73.9-94.5%); success 40/40; failures 0 ({})
  - latency: n=40, median 4785.5 ms, min 2829.0, max 8806.0, p95 6657.0 ms
  - drafts: restore-exact 40/40 = 100.0% (95% CI 91.2-100.0%); no placeholders 7; unknown-placeholder drafts 0; gold-value drafts 0; median length 246
  - rubric (n=10): relevance 2.0, placeholder 2.0, German 1.6
- **claude-cli**: joint 28/40 = 70.0% (95% CI 54.6-81.9%); category 35/40 = 87.5% (95% CI 73.9-94.5%); urgency 33/40 = 82.5% (95% CI 68.1-91.3%); success 40/40; failures 0 ({})
  - latency: n=40, median 5222.0 ms, min 4056.0, max 56766.0, p95 33243.0 ms
  - drafts: restore-exact 40/40 = 100.0% (95% CI 91.2-100.0%); no placeholders 1; unknown-placeholder drafts 0; gold-value drafts 0; median length 297
  - rubric (n=10): relevance 2.0, placeholder 2.0, German 1.6
- **codex-cli**: joint 25/40 = 62.5% (95% CI 47.0-75.8%); category 32/40 = 80.0% (95% CI 65.2-89.5%); urgency 33/40 = 82.5% (95% CI 68.1-91.3%); success 40/40; failures 0 ({})
  - latency: n=40, median 6667.5 ms, min 4868.0, max 41377.0, p95 32949.0 ms
  - drafts: restore-exact 40/40 = 100.0% (95% CI 91.2-100.0%); no placeholders 11; unknown-placeholder drafts 0; gold-value drafts 0; median length 214
  - rubric (n=10): relevance 2.0, placeholder 2.0, German 1.6

## Limitations

- Synthetic template enquiries, not real customer mail.
- 40 enquiries is small; intervals are wide.
- Urgency is confounded with category in the corpus.
- One sample per backend per enquiry; LLM output is not deterministic.
- Latency is one machine and one network path.
- One reviewer scores the draft rubric.
- No pass thresholds; these are descriptive measurements of pseudonymisation,
  not a compliance or legal claim.
