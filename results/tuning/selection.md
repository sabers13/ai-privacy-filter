# B2 profile selection (dev only)

Protocol hash: `f76b5b8ada0f3f31666aae7abc00d6fb7d845a0ba3f4056c41c6a6ade67d2f86`.

The selection rule is pre-registered in METHOD.md §4.2: highest minimum of the nine dev recalls (seven synthetic types, GermEval PERSON and ORG); ties by lower enquiry-level over-masking, higher macro precision, then `balanced` before `strict`.

| candidate | r_min | over-masking (enquiries) | macro precision |
|---|---|---|---|
| balanced | 69.2% | 0.0% | 93.0% |
| strict | 69.4% | 0.0% | 92.9% |

Per type and split:

| candidate | synthetic/PERSON | synthetic/ORG | synthetic/EMAIL | synthetic/PHONE | synthetic/IBAN | synthetic/ADDRESS_STREET | synthetic/POSTCODE_CITY | germeval/PERSON | germeval/ORG |
|---|---|---|---|---|---|---|---|---|---|
| balanced | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 88.5% | 69.2% |
| strict | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 89.2% | 69.4% |

**Winner: `strict`** (r_min 69.4%).
