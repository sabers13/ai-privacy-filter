# Third-party components

This file records the components the project uses, with their licences. The
direct runtime and development dependencies are pinned in `uv.lock`; the most
important transitive packages are listed too. Nothing here is redistributed by
this repository unless the entry says so.

| Component | Licence | Use |
|---|---|---|
| Microsoft Presidio (analyzer, anonymizer) | MIT | detection and anonymisation primitives |
| spaCy | MIT | NLP pipeline |
| spaCy model `de_core_news_lg` 3.8.0 | MIT | German NER and tokenisation |
| Faker | MIT | synthetic enquiry generation and the German first-name gazetteer |
| FastAPI | MIT | local HTTP service |
| Starlette | BSD-3-Clause | FastAPI's ASGI toolkit |
| uvicorn | BSD-3-Clause | ASGI server |
| pydantic | MIT | contracts and validation |
| httpx | BSD-3-Clause | OpenAI-compatible backend client |
| AnyIO | MIT | async compatibility layer used by Starlette |
| phonenumbers | Apache-2.0 | phone-number parsing inside Presidio |
| tldextract | BSD-3-Clause | URL/domain handling inside Presidio |
| requests | Apache-2.0 | HTTP client used by Presidio and tldextract |
| numpy | BSD-3-Clause (with 0BSD, MIT, Zlib and CC0-1.0 parts) | numeric dependency of spaCy/thinc |
| PyYAML | MIT | reading `project-facts.yml` and the compose file in tests |
| pytest, pytest-cov, pytest-socket | MIT | tests |
| hypothesis | MPL-2.0 | property-based tests |
| ruff | MIT | lint and format |
| mypy | MIT | static typing |
| uv and uv_build | MIT OR Apache-2.0 | dependency resolution, virtual environment and build backend |
| gitleaks | MIT | secret scan on the developer machine |
| GermEval 2014 NER (Benikova, Biemann, Reznicek, LREC 2014) | CC BY 4.0 | held-out detection evaluation only; downloaded by `data/get_germeval.py`, not redistributed, no sentences shown |
| n8n (self-hosted) | Sustainable Use License | local workflow orchestration and human review |
| Ollama | MIT | optional local model runtime |
| Qwen2.5 1.5B Instruct | Apache-2.0 | optional local triage model via Ollama; downloaded at run time, not redistributed |
| SaberOps worker adapters (portions) | Apache-2.0 | origin of the backend adapter pattern; attribution headers retained |

SaberOps is the origin of the backend adapter pattern and is licensed under
Apache-2.0. Attribution headers are kept where code is ported.

The self-hosted Inter typeface used by the separate public page is under the SIL
Open Font License 1.1; it is not part of this repository.

GermEval 2014 is used as a numbers-only evaluation set. The downloader verifies
pinned SHA-256 values and byte sizes, the data lives under the git-ignored
`data/external/` directory, and no sentence, token run or entity value from it
is committed or sent to a model.
