# Data sources

External data used for evaluation is downloaded into `data/external/` and is
git-ignored. It is never committed, redistributed, shown on the page, or sent to
an LLM or any other network service. The files are pinned by size and SHA-256
below; `data/get_germeval.py` verifies both before parsing.

## GermEval 2014 NER

| | |
|---|---|
| Dataset | GermEval 2014 Named Entity Recognition Shared Task |
| Homepage | <https://sites.google.com/site/germeval2014ner/home> |
| Data and task setup | <https://sites.google.com/site/germeval2014ner/data> |
| Citation | Benikova, Biemann, Reznicek. *NoSta-D Named Entity Annotation for German: Guidelines and Dataset.* LREC 2014, pp. 2524–2531. <http://www.lrec-conf.org/proceedings/lrec2014/pdf/276_Paper.pdf> |
| Licence | CC BY 4.0 — <https://creativecommons.org/licenses/by/4.0/> |
| Retrieved | 2026-10-27 |
| Revision | Current revision after the page's 28/4/22 encoding fix; checksums pin it |

### Download route

The data page links one Google Drive folder:
<https://drive.google.com/drive/folders/1kC0I2UGl2ltrluI9NqDjaQJGw5iliw_J?usp=sharing>.
The folder was listed non-interactively (`embeddedfolderview`) on the retrieval
date, which confirms the three published filenames and ids below. Each split was
then fetched directly from its Drive file id with `curl` and `httpx` (`HTTP 200`,
no interactive confirmation page), so the Hugging Face mirror
`GermanEval/germeval_14` was **not** needed and its dataset loading script —
which requires arbitrary Python execution — was never run. A labelled and an
unlabelled test file are both published; the loader uses the labelled
`NER-de-test.tsv`. The mirror is an unchanged copy of the same CC BY 4.0 data and
remains the documented fallback if the Drive links stop resolving
non-interactively.

### Pinned files

| split | file | url | bytes | sentences | sha256 |
|---|---|---|---|---|---|
| train | NER-de-train.tsv | https://drive.google.com/uc?export=download&id=1Jjhbal535VVz2ap4v4r_rN1UEHTdLK5P | 7882321 | 24000 | `98682c21700e5bd7959ac361150679929e4af5d91e4dd7c379cc89133a810994` |
| dev | NER-de-dev.tsv | https://drive.google.com/uc?export=download&id=1ZfRcQThdtAR5PPRjIDtrVP7BtXSCUBbm | 723854 | 2200 | `2ffe8405acc6bb6111b62a88623aa1e1f2e6db16b77fc005c54163541807aa05` |
| test | NER-de-test.tsv | https://drive.google.com/uc?export=download&id=1u9mb7kNJHWQCWyweMDRMuTFoOHOfeBTH | 1682725 | 5100 | `ec6e6adce5ed71cee119ae758e569ea4be54c6be5319b3b49dfcdcb05e51301b` |

The sentence counts match the published split sizes (24,000 / 2,200 / 5,100).

### Parsed counts

`data/get_germeval.py` joins the tokens with single spaces and keeps outer-level
spans only. `spans` counts every outer span; `primary` counts `PERSON` and `ORG`,
the classes scored as personal data.

| split | sentences | tokens | spans | primary |
|---|---|---|---|---|
| train | 24000 | 452853 | 29078 | 12934 |
| dev | 2200 | 41653 | 2674 | 1207 |
| test | 5100 | 96499 | 6178 | 2789 |

### Format and mapping

One sentence is a `#` header line (`source`, `[date]`) followed by one token per
line with four tab-separated columns: token index, token, outer BIO tag, nested
BIO tag. Blank lines separate sentences. The nested column is ignored, because
this project evaluates outer spans.

| GermEval outer tag | Loader class | Contract type | Primary metric |
|---|---|---|---|
| `PER` | `PERSON` | `EntityType.PERSON` | yes |
| `ORG` | `ORG` | `EntityType.ORG` | yes |
| `LOC` | `LOCATION` | none | no — reported separately |
| `OTH` | `OTHER` | none | no |
| `*deriv` | own `*_DERIV` class | none | no |
| `*part` | own `*_PART` class | none | no |

Locations are reported separately rather than as personal data: a place name on
its own does not identify a person. Derived and part tags are annotation
sub-structure, so they are kept as separate classes and excluded from the
primary metric.

### Reproduce

```sh
uv run python data/get_germeval.py             # download, verify, parse
uv run python data/get_germeval.py --verify-only
```

Outputs, all under `data/external/germeval/` and never committed:
`NER-de-train.tsv`, `NER-de-dev.tsv`, `NER-de-test.tsv`, `parsed/<split>.jsonl`,
`parsed/<split>.stats.json` and `manifest.json`.

## Model and runtime sources

Primary sources for the model and runtime used by the optional local path, with
the licence terms that apply. Accessed 2026-10-27.

### Ollama 0.40.11

- Release artefacts and published checksums:
  <https://github.com/ollama/ollama/releases/tag/v0.40.11>
  (`ollama-linux-amd64.tar.zst`, `sha256sum.txt`).
- Licence: MIT.
- Use here: optional local model runtime on a separate host, reached over an SSH
  tunnel (T07). The binary is downloaded on that host at install time, not
  redistributed with this repository.
- Checksum recorded at install time:
  `7bedab90dd122661be78798e9f0edf37fccf0cccf2699d2c58ea1a3afdc04b6d`
  (verified against the published `sha256sum.txt`).

### Qwen2.5 1.5B Instruct

- Model page: <https://ollama.com/library/qwen2.5> and
  <https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct>.
- Licence: Apache-2.0. The Hugging Face model card metadata and the Ollama
  library page both state that every Qwen2.5 size except 3B and 72B is released
  under Apache-2.0. Quoted from the Ollama library page: "all models except the
  3B and 72B are released under the Apache 2.0 license, while the 3B and 72B
  models are under the Qwen license."
- Use here: default local triage model (`qwen2.5:1.5b`, 986 MB on disk) for the
  optional Ollama path. Apache-2.0 permits this use, including commercial use,
  without a separate agreement.
- Fallback considered: `qwen2.5:0.5b`, same licence family and Apache-2.0. Not
  needed in the T07 measurement.
