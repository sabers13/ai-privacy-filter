# Evaluation reruns

Append-only record of test-split access, written by `eval/detection.py` before reading a test record (METHOD.md §5.4). The `finished_at` cell stays `-` in the row written before the run; the completion log below records the end time as a separate append, so no historical row is ever rewritten.

| # | finished_at | run_id | stage | commit | method | changed_since | reason | test_seen_before_change | approval |
|---|---|---|---|---|---|---|---|---|---|
| 1 | - | 20261027T131852Z-detection-67d6ac9 | detection | 67d6ac9 | f76b5b8ada0f | n/a (first run of this stage) | first run | no | - |

## Completion log
- 20261027T131852Z-detection-67d6ac9 finished at 2026-10-27T13:21:27Z (complete)
