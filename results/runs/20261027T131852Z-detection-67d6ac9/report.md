# Detection evaluation — synthetic test and GermEval 2014 test

Protocol: `METHOD.md`, SHA-256 `f76b5b8ada0f3f31666aae7abc00d6fb7d845a0ba3f4056c41c6a6ade67d2f86`. B2 configuration: `strict` (config SHA-256 `68a8b18ac97220e2df849ec9dc3014bf5113054a6a2dd87116f535857e2e6dba`).

Data: synthetic test, 160 enquiries, 1074 gold spans, SHA-256 `1751adafa08dc54c7389c044651b1337f4d7d8dcc6e75ec947e7f53b3ddb133b`; GermEval 2014 test, 5100 sentences, manifest SHA-256 `9bf93fedb4b57e38a48b546d67e82f4f2d9ff3367339035e9aed83e1728389f1`.

Environment: Python 3.12.3, presidio-analyzer 2.2.366, spaCy 3.8.18, de_core_news_lg-3.8.0, linux-x86_64. Network: blocked during scoring (after data download and cache warm-up).

This is one test-split run at stage `detection`. The run id, commit, timestamps and artifact hashes are in `results/runs/<run-id>/run.json`; the rerun table is `docs/eval-reruns.md`. No number here is tuned on test data, and there are no pass thresholds: the filter is measured as it is.

## The four answers

**1. Caught.** B2 protects 1069 of 1074 gold spans across the seven estimable types (99.5%, 95 % CI 98.9-99.8). The worst type is POSTCODE_CITY (134/137, 97.8%, 95 % CI 93.8-99.3). No simpler baseline wins a type.

**2. Leaked.** B2 leaked 3 of 160 synthetic test enquiries (1.9%, 95 % CI 0.6-5.4, upper bound 5.4%). 4 gold spans were missed and 1 was only partly covered; leaked types: POSTCODE_CITY (3). The production guard blocked 0 enquiries; the difference between leaks and blocks is the missed-value path. Baselines: B0 160/160; B1 145/160.

**3. Over-masked.** 0 of 160 synthetic test enquiries had at least one extra character (0.0%, 95 % CI 0.0-2.3); 0 of 19824 replaced characters were extra, and 0.0% of replaced characters were outside a gold span (cluster bootstrap 0.0-0.0). The worst type is no type.

**4. Backend quality.** Not measured in this stage: the fixed 40-enquiry backend run belongs to T12 and is currently not measured (the fixed 40-enquiry backend run lands with T12).

## Synthetic test — recall (primary, compatible-type coverage)

A gold span is protected only when every character is inside a prediction of a compatible type; adjacent predictions may combine. `strict` ignores the PERSON/ORG and EMAIL/URL relaxations. B0 has no PERSON or ORG rule, so its 0 % there is by construction, not a measurement failure.

### B0 — regex-only

The page try-it rule set: fixed patterns, no model, no name list and no PERSON or ORG rule.

<!-- table:synthetic-recall:B0 -->
| type | gold | protected | recall | 95% CI | strict protected | strict recall | partial | missed |
|---|---|---|---|---|---|---|---|---|
| PERSON | 252 | 0 | 0.0% | 0.0-1.5 | 0 | 0.0% | 0 | 252 |
| ORG | 114 | 0 | 0.0% | 0.0-3.3 | 0 | 0.0% | 0 | 114 |
| EMAIL | 206 | 206 | 100.0% | 98.2-100.0 | 206 | 100.0% | 0 | 0 |
| PHONE | 160 | 160 | 100.0% | 97.7-100.0 | 160 | 100.0% | 0 | 0 |
| IBAN | 68 | 46 | 67.6% | 55.8-77.6 | 46 | 67.6% | 0 | 22 |
| ADDRESS_STREET | 137 | 70 | 51.1% | 42.8-59.3 | 70 | 51.1% | 34 | 33 |
| POSTCODE_CITY | 137 | 130 | 94.9% | 89.8-97.5 | 130 | 94.9% | 7 | 0 |
| URL | 0 | 0 | not estimable | - | 0 | not estimable | 0 | 0 |
| VAT_ID | 0 | 0 | not estimable | - | 0 | not estimable | 0 | 0 |
| DATE_OF_BIRTH | 0 | 0 | not estimable | - | 0 | not estimable | 0 | 0 |

### B1 — Presidio defaults + German spaCy

Presidio's default recognizer registry with the pinned German pipeline, no custom recognizers, no cleanup and no merge step.

<!-- table:synthetic-recall:B1 -->
| type | gold | protected | recall | 95% CI | strict protected | strict recall | partial | missed |
|---|---|---|---|---|---|---|---|---|
| PERSON | 252 | 180 | 71.4% | 65.6-76.7 | 180 | 71.4% | 60 | 12 |
| ORG | 114 | 19 | 16.7% | 10.9-24.6 | 19 | 16.7% | 40 | 55 |
| EMAIL | 206 | 206 | 100.0% | 98.2-100.0 | 206 | 100.0% | 0 | 0 |
| PHONE | 160 | 124 | 77.5% | 70.4-83.3 | 124 | 77.5% | 16 | 20 |
| IBAN | 68 | 46 | 67.6% | 55.8-77.6 | 46 | 67.6% | 0 | 22 |
| ADDRESS_STREET | 137 | 0 | 0.0% | 0.0-2.7 | 0 | 0.0% | 0 | 137 |
| POSTCODE_CITY | 137 | 0 | 0.0% | 0.0-2.7 | 0 | 0.0% | 0 | 137 |
| URL | 0 | 0 | not estimable | - | 0 | not estimable | 0 | 0 |
| VAT_ID | 0 | 0 | not estimable | - | 0 | not estimable | 0 | 0 |
| DATE_OF_BIRTH | 0 | 0 | not estimable | - | 0 | not estimable | 0 | 0 |

### B2 — full configuration

Product detector: German NER, Presidio email/URL, every German recognizer, the shared merge rule and the frozen `strict` profile.

<!-- table:synthetic-recall:B2 -->
| type | gold | protected | recall | 95% CI | strict protected | strict recall | partial | missed |
|---|---|---|---|---|---|---|---|---|
| PERSON | 252 | 252 | 100.0% | 98.5-100.0 | 252 | 100.0% | 0 | 0 |
| ORG | 114 | 114 | 100.0% | 96.7-100.0 | 114 | 100.0% | 0 | 0 |
| EMAIL | 206 | 206 | 100.0% | 98.2-100.0 | 206 | 100.0% | 0 | 0 |
| PHONE | 160 | 160 | 100.0% | 97.7-100.0 | 160 | 100.0% | 0 | 0 |
| IBAN | 68 | 68 | 100.0% | 94.7-100.0 | 68 | 100.0% | 0 | 0 |
| ADDRESS_STREET | 137 | 135 | 98.5% | 94.8-99.6 | 135 | 98.5% | 1 | 1 |
| POSTCODE_CITY | 137 | 134 | 97.8% | 93.8-99.3 | 134 | 97.8% | 0 | 3 |
| URL | 0 | 0 | not estimable | - | 0 | not estimable | 0 | 0 |
| VAT_ID | 0 | 0 | not estimable | - | 0 | not estimable | 0 | 0 |
| DATE_OF_BIRTH | 0 | 0 | not estimable | - | 0 | not estimable | 0 | 0 |

Recall leaders (all three baselines): PERSON: B2 leads recall by 28.6 points over B1 (100.0% vs 71.4%) · ORG: B2 leads recall by 83.3 points over B1 (100.0% vs 16.7%) · EMAIL: B2 ties B0, B1 at 100.0% · PHONE: B2 ties B0 at 100.0% · IBAN: B2 leads recall by 32.4 points over B0 (100.0% vs 67.6%) · ADDRESS_STREET: B2 leads recall by 47.4 points over B0 (98.5% vs 51.1%) · POSTCODE_CITY: B2 leads recall by 2.9 points over B0 (97.8% vs 94.9%).

## Synthetic test — precision

### B0 — regex-only

<!-- table:synthetic-precision:B0 -->
| type | predicted | correct | precision | 95% CI | exact | exact precision |
|---|---|---|---|---|---|---|
| PERSON | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |
| ORG | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |
| EMAIL | 206 | 206 | 100.0% | 98.2-100.0 | 206 | 100.0% |
| PHONE | 164 | 160 | 97.6% | 93.9-99.0 | 160 | 97.6% |
| IBAN | 46 | 46 | 100.0% | 92.3-100.0 | 46 | 100.0% |
| ADDRESS_STREET | 104 | 104 | 100.0% | 96.4-100.0 | 70 | 67.3% |
| POSTCODE_CITY | 137 | 137 | 100.0% | 97.3-100.0 | 130 | 94.9% |
| URL | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |
| VAT_ID | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |
| DATE_OF_BIRTH | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |

Precision counts a prediction as correct only when its whole range is inside a compatible gold span; an over-long prediction therefore counts as over-masking, not as correct. A prediction split into two pieces inside one gold span counts twice, which is the stated cost of counting span occurrences. `exact` requires both boundaries to match.

### B1 — Presidio defaults + German spaCy

<!-- table:synthetic-precision:B1 -->
| type | predicted | correct | precision | 95% CI | exact | exact precision |
|---|---|---|---|---|---|---|
| PERSON | 271 | 200 | 73.8% | 68.3-78.7 | 140 | 51.7% |
| ORG | 61 | 34 | 55.7% | 43.3-67.5 | 10 | 16.4% |
| EMAIL | 206 | 206 | 100.0% | 98.2-100.0 | 206 | 100.0% |
| PHONE | 160 | 140 | 87.5% | 81.5-91.8 | 124 | 77.5% |
| IBAN | 46 | 46 | 100.0% | 92.3-100.0 | 46 | 100.0% |
| ADDRESS_STREET | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |
| POSTCODE_CITY | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |
| URL | 224 | 206 | 92.0% | 87.7-94.9 | 0 | 0.0% |
| VAT_ID | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |
| DATE_OF_BIRTH | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |

Precision counts a prediction as correct only when its whole range is inside a compatible gold span; an over-long prediction therefore counts as over-masking, not as correct. A prediction split into two pieces inside one gold span counts twice, which is the stated cost of counting span occurrences. `exact` requires both boundaries to match.

### B2 — full configuration

<!-- table:synthetic-precision:B2 -->
| type | predicted | correct | precision | 95% CI | exact | exact precision |
|---|---|---|---|---|---|---|
| PERSON | 253 | 252 | 99.6% | 97.8-99.9 | 252 | 99.6% |
| ORG | 114 | 114 | 100.0% | 96.7-100.0 | 114 | 100.0% |
| EMAIL | 206 | 206 | 100.0% | 98.2-100.0 | 206 | 100.0% |
| PHONE | 160 | 160 | 100.0% | 97.7-100.0 | 160 | 100.0% |
| IBAN | 68 | 68 | 100.0% | 94.7-100.0 | 68 | 100.0% |
| ADDRESS_STREET | 136 | 136 | 100.0% | 97.3-100.0 | 135 | 99.3% |
| POSTCODE_CITY | 134 | 134 | 100.0% | 97.2-100.0 | 134 | 100.0% |
| URL | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |
| VAT_ID | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |
| DATE_OF_BIRTH | 0 | 0 | n/a (0 predictions) | - | 0 | n/a (0 predictions) |

Precision counts a prediction as correct only when its whole range is inside a compatible gold span; an over-long prediction therefore counts as over-masking, not as correct. A prediction split into two pieces inside one gold span counts twice, which is the stated cost of counting span occurrences. `exact` requires both boundaries to match.

Precision leaders (all three baselines): PERSON: B2 leads precision by 25.8 points over B1 (99.6% vs 73.8%) · ORG: B2 leads precision by 44.3 points over B1 (100.0% vs 55.7%) · EMAIL: B2 ties B0, B1 at 100.0% · PHONE: B2 leads precision by 2.4 points over B0 (100.0% vs 97.6%) · IBAN: B2 ties B0, B1 at 100.0% · ADDRESS_STREET: B2 ties B0 at 100.0% · POSTCODE_CITY: B2 ties B0 at 100.0%.

## GermEval 2014 test — PERSON and ORG

Each of the 5100 sentences is evaluated independently. Only the outer annotation column is used. No GermEval text is shown, stored in a result or sent to a model.

### B0 — regex-only

<!-- table:germeval:B0 -->
| type | gold | protected | recall | 95 % CI | strict protected | strict recall | precision | exact precision |
|---|---|---|---|---|---|---|---|---|
| PERSON | 1639 | 0 | 0.0% | 0.0-0.2 | 0 | 0.0% | n/a | n/a |
| ORG | 1150 | 0 | 0.0% | 0.0-0.3 | 0 | 0.0% | n/a | n/a |
| PERSON+ORG (micro) | 2789 | 0 | 0.0% | 0.0-0.1 | 0 | 0.0% | - | - |

<!-- table:germeval-location:B0 -->
| LOCATION gold | covered by any personal-data span | incidental coverage | 95 % CI |
|---|---|---|---|
| 1706 | 6 | 0.4% | 0.2-0.8 |

LOCATION is not a detection target: `EntityType` has no LOCATION member and the detector never emits one, so its class recall is 0 % by construction. The incidental coverage above is informational and is never folded into personal-data recall. `OTHER`, `deriv` and `part` classes are not scored. A prediction split inside one gold span counts twice in precision.

### B1 — Presidio defaults + German spaCy

<!-- table:germeval:B1 -->
| type | gold | protected | recall | 95 % CI | strict protected | strict recall | precision | exact precision |
|---|---|---|---|---|---|---|---|---|
| PERSON | 1639 | 1419 | 86.6% | 84.8-88.1 | 1400 | 85.4% | 83.3% | 80.7% |
| ORG | 1150 | 745 | 64.8% | 62.0-67.5 | 718 | 62.4% | 58.9% | 56.7% |
| PERSON+ORG (micro) | 2789 | 2164 | 77.6% | 76.0-79.1 | 2118 | 75.9% | - | - |

<!-- table:germeval-location:B1 -->
| LOCATION gold | covered by any personal-data span | incidental coverage | 95 % CI |
|---|---|---|---|
| 1706 | 96 | 5.6% | 4.6-6.8 |

LOCATION is not a detection target: `EntityType` has no LOCATION member and the detector never emits one, so its class recall is 0 % by construction. The incidental coverage above is informational and is never folded into personal-data recall. `OTHER`, `deriv` and `part` classes are not scored. A prediction split inside one gold span counts twice in precision.

### B2 — full configuration

<!-- table:germeval:B2 -->
| type | gold | protected | recall | 95 % CI | strict protected | strict recall | precision | exact precision |
|---|---|---|---|---|---|---|---|---|
| PERSON | 1639 | 1438 | 87.7% | 86.1-89.2 | 1419 | 86.6% | 81.4% | 78.6% |
| ORG | 1150 | 748 | 65.0% | 62.2-67.7 | 720 | 62.6% | 58.9% | 56.8% |
| PERSON+ORG (micro) | 2789 | 2186 | 78.4% | 76.8-79.9 | 2139 | 76.7% | - | - |

<!-- table:germeval-location:B2 -->
| LOCATION gold | covered by any personal-data span | incidental coverage | 95 % CI |
|---|---|---|---|
| 1706 | 108 | 6.3% | 5.3-7.6 |

LOCATION is not a detection target: `EntityType` has no LOCATION member and the detector never emits one, so its class recall is 0 % by construction. The incidental coverage above is informational and is never folded into personal-data recall. `OTHER`, `deriv` and `part` classes are not scored. A prediction split inside one gold span counts twice in precision.

Recall leaders (all three baselines): PERSON: B2 leads recall by 1.1 points over B1 (87.7% vs 86.6%) · ORG: B2 leads recall by 0.2 points over B1 (65.0% vs 64.8%).

## Leak rate (synthetic test, independent gold audit)

The audit checks the exact prompt string against the gold values, not against the detector's mapping, so a missed value is caught even though the production guard cannot see it. Phone and IBAN values are also compared digit by digit (at least four digits). No leaking prompt is stored.

<!-- table:leak -->
| baseline | leaked | leak rate | 95 % CI | missed gold spans | guard blocks | residual chars | residual rate | bootstrap CI |
|---|---|---|---|---|---|---|---|---|
| B0 | 160 | 100.0% | 97.7-100.0 | 421 | 0 | 7853 | 39.5% | 38.2-40.9 |
| B1 | 145 | 90.6% | 85.1-94.2 | 383 | 2 | 7971 | 40.1% | 37.8-42.3 |
| B2 | 3 | 1.9% | 0.6-5.4 | 4 | 0 | 70 | 0.4% | 0.0-0.8 |

Residual characters are gold characters not inside any compatible prediction, divided by all gold characters; the interval is a 10,000-resample enquiry-cluster bootstrap (seed 20261027). A guard block and a leak are different events: the guard only knows values the detector mapped.

## Over-masking (synthetic test)

Extra characters are inside a prediction but outside every gold span of any type. The enquiry-level interval is Wilson; the character-level interval is an enquiry-cluster bootstrap.

<!-- table:overmask -->
| baseline | enquiries with extra | enquiry rate | 95 % CI | extra chars | replaced chars | character rate | bootstrap CI | false spans | predictions |
|---|---|---|---|---|---|---|---|---|---|
| B0 | 0 | 0.0% | 0.0-2.3 | 0 | 12065 | 0.0% | 0.0-0.0 | 0 | 657 |
| B1 | 70 | 43.8% | 36.3-51.5 | 817 | 13203 | 6.2% | 4.7-7.8 | 23 | 968 |
| B2 | 0 | 0.0% | 0.0-2.3 | 0 | 19824 | 0.0% | 0.0-0.0 | 0 | 1071 |

<!-- table:overmask-type -->
| baseline | predicted type | extra characters |
|---|---|---|
| B1 | PERSON | 752 |
| B1 | ORG | 65 |

## Restore round-trip (synthetic test, B2)

`restore(redact(text)).text == text` byte for byte. Missed values stay in the text on both sides, so this measures redaction/restoration fidelity, not recall. A failure caused by the same value appearing with different casing or spacing is reported as `normalisation variant`; no value is printed.

<!-- table:restore -->
| exact round-trips | total | exactness | 95 % CI | failures |
|---|---|---|---|---|
| 160 | 160 | 100.0% | 97.7-100.0 | 0 |

Draft restoration is per backend and belongs to the T12 run: not measured (the fixed 40-enquiry backend run lands with T12).

## Ten most common miss patterns (synthetic test)

Patterns are derived from B2 misses on the synthetic test split. Examples are synthetic corpus text, marked with «guillemets» around the gold value; they are fictional Faker values and templates, not real people or companies.

<!-- table:miss-patterns -->
| # | pattern | type | count | share of type misses | example (synthetic) |
|---|---|---|---|---|---|
| 1 | postcode-city-not-matched | POSTCODE_CITY | 3 | 100.0% | syn-test-0006: …nung bitte an: van der Dussenring 7 «00523 Eisleben» IBAN: DE45474642234746224345 |
| 2 | partial-boundary-address_street | ADDRESS_STREET | 1 | 50.0% | syn-test-0014: …en Mikhail Vogel Vogel Systeme GmbH «van der Dussenplatz 62» 46519 Nürtingen Telefon: +49 (0)64… |
| 3 | street-name-with-space | ADDRESS_STREET | 1 | 50.0% | syn-test-0006: …ieferzeiten zu. Rechnung bitte an: «van der Dussenring 7» 00523 Eisleben IBAN: DE454746422347… |

B2 produced 3 distinct miss patterns on this corpus, short of ten. To keep the ten-pattern deliverable without inventing product misses, the table below lists B1's ten most common miss patterns on the same synthetic records. They are an untuned baseline's failures, not B2's; they show where a simpler detector breaks, and no value in them is real.

<!-- table:miss-patterns-b1 -->
| # | pattern | type | count | share of type misses | example (synthetic) |
|---|---|---|---|---|---|
| 1 | postcode-city-not-matched | POSTCODE_CITY | 137 | 100.0% | syn-test-0001: Andy Drub Bohlanderweg 839 «27333 Böblingen» E-Mail: odaebert@example.com Telefo… |
| 2 | street-not-matched | ADDRESS_STREET | 70 | 51.1% | syn-test-0001: Andy Drub «Bohlanderweg 839» 27333 Böblingen E-Mail: odaebert@ex… |
| 3 | street-house-number-range | ADDRESS_STREET | 65 | 47.4% | syn-test-0002: …wir weiter vorgehen können. Straße: «Stollstr. 34-10» PLZ/Ort: 94706 Wurzen |
| 4 | partial-boundary-person | PERSON | 60 | 83.3% | syn-test-0008: «Meral Albers B.A.» Patrizia-Röhrdanz-Ring 36 35676 Kul… |
| 5 | org-legal-form-not-anchored | ORG | 52 | 54.7% | syn-test-0003: …Mit freundlichen Grüßen Geza Dörr «Weitblick Medien GmbH» Gerfried-Hiller-Gasse 907 98248 Gör… |
| 6 | partial-boundary-org | ORG | 40 | 42.1% | syn-test-0002: …efon: +49(0)1008 32324 Unternehmen: «Weitblick Medien GmbH» Nachricht: Bei der Anmeldung im Kun… |
| 7 | iban-wrapped-across-lines | IBAN | 22 | 100.0% | syn-test-0007: …E-Mail: leonid96@example.com IBAN: «DE76 7794 4898 0223 9735 58» |
| 8 | partial-boundary-phone | PHONE | 16 | 44.4% | syn-test-0002: …l: salzphilipp@example.org Telefon: «+49(0)1008 32324» Unternehmen: Weitblick Medien GmbH… |
| 9 | phone-not-matched | PHONE | 10 | 27.8% | syn-test-0011: …zallee 5817 89841 Miesbach Telefon: «00411 90712» |
| 10 | phone-with-prefix-or-brackets | PHONE | 10 | 27.8% | syn-test-0026: …l: ekruschwitz@example.net Telefon: «+49(0)0177 258033» |

## GermEval misses by category (no sentence quoted)

GermEval misses are aggregated by gold type and coverage category. No sentence, token run or entity value from GermEval appears here or anywhere in `results/`.

<!-- table:germeval-miss-categories -->
| gold type | category | count |
|---|---|---|
| ORG | no overlapping prediction | 372 |
| ORG | partial coverage by the other name type | 7 |
| ORG | partial coverage, same type, boundary differs | 23 |
| PERSON | no overlapping prediction | 170 |
| PERSON | partial coverage by the other name type | 1 |
| PERSON | partial coverage, same type, boundary differs | 30 |

## Limitations

- Synthetic enquiries are template-generated German, not real customer mail.
- 160 test enquiries and 40 triage enquiries are small.
- GermEval is Wikipedia/news text, not enquiries, and has no EMAIL, PHONE or IBAN labels.
- Wilson intervals treat spans as trials and may be optimistic; the cluster bootstrap is the sensitivity check.
- Urgency is confounded with category in the synthetic corpus.
- One sample per backend per enquiry; LLM output is not deterministic.
- Latency is one machine and one network path, not a benchmark.
- One reviewer scores the draft rubric.
- There are no pass thresholds. These are descriptive measurements of pseudonymisation, not a compliance or legal claim. This is not legal advice.

## Reproduction

`make reproduce` re-verifies the GermEval download checksums, recomputes every number from the committed corpus and the verified download with the network blocked, and compares the result files byte for byte. The result files carry no timestamp, run id or commit, so a rebase cannot change them.
