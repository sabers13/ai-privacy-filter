# Synthetic enquiries

Synthetic data only. Names, organisations, street addresses, phone numbers,
e-mail addresses and IBANs are produced by a seeded Faker `de_DE` instance;
the message bodies are hand-written German templates. Nothing here comes from
a real person, a real company or a real account.

- Generator: `data/make_synthetic.py` v1.0.0
- Seed: 20261029
- Seed note: seed changed: scan hit
- Faker locale: `de_DE`
- Records: dev 80, test 160

Regenerate:

    uv run python data/make_synthetic.py --seed 20261029

## Counts by style

| Style | dev | test |
|---|---|---|
| Formal letter | 12 | 23 |
| Short web-form message | 12 | 23 |
| E-mail with signature block | 12 | 23 |
| Forwarded e-mail with quoted header | 11 | 23 |
| Informal du-form | 11 | 23 |
| Mixed: two people and a company | 11 | 23 |
| Hard cases: word surnames, name in company, split IBAN, odd phones | 11 | 22 |

## Counts by category

| Category | dev | test |
|---|---|---|
| QUOTE_REQUEST | 16 | 32 |
| SUPPORT | 16 | 32 |
| JOB_APPLICATION | 16 | 32 |
| PARTNERSHIP | 16 | 32 |
| OTHER | 16 | 32 |

## Counts by urgency

| Urgency | dev | test |
|---|---|---|
| LOW | 16 | 32 |
| NORMAL | 48 | 96 |
| HIGH | 16 | 32 |

## Gold spans by type

| Entity type | dev | test |
|---|---|---|
| PERSON | 125 | 252 |
| ORG | 57 | 114 |
| EMAIL | 102 | 206 |
| PHONE | 80 | 160 |
| IBAN | 34 | 68 |
| ADDRESS_STREET | 69 | 137 |
| POSTCODE_CITY | 69 | 137 |
| URL | 0 | 0 |
| VAT_ID | 0 | 0 |
| DATE_OF_BIRTH | 0 | 0 |

## Notes

- `gold_spans` offsets are half-open and character-exact: `text[start:end] == span.text`.
- Categories and urgency follow explicit cues in the text: deadlines, `dringend` and complaint tone mark HIGH; `es eilt nicht`, `keine Eile` and `unverbindlich` mark LOW; everything else is NORMAL.
- The `hard_cases` records contain surnames that are common words, a person name inside a company name, an IBAN broken across a line and odd phone formats.
- The data is regenerated only from the seed above. A different Faker version may change individual values.
