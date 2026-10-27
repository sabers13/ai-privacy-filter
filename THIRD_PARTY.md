# Third-party components

This file records the components the project uses, with their licences. Entries
marked *planned* are not yet integrated; the task that integrates them updates
this file.

| Component | Licence | Use |
|---|---|---|
| Microsoft Presidio (analyzer, anonymizer) | MIT | detection and anonymisation primitives |
| spaCy | MIT | NLP pipeline |
| spaCy model `de_core_news_lg` 3.8.0 | MIT | German NER and tokenisation |
| Faker | MIT | synthetic enquiry generation (planned, T04) |
| FastAPI | MIT | local HTTP service (planned, T08) |
| uvicorn | BSD-3-Clause | ASGI server (planned, T08) |
| pydantic | MIT | contracts |
| httpx | BSD-3-Clause | OpenAI-compatible backend client (planned, T06) |
| pytest, pytest-cov, pytest-socket, hypothesis | MIT | tests |
| ruff | MIT | lint and format |
| mypy | MIT | static typing |
| gitleaks | MIT | secret scan (developer machine only) |
| GermEval 2014 NER (Benikova, Biemann, Reznicek, LREC 2014) | CC BY 4.0 | held-out detection evaluation only; downloaded by `data/get_germeval.py`, not redistributed, no sentences shown (T05) |
| n8n (self-hosted) | Sustainable Use License | local workflow orchestration (planned, T13) |
| Ollama | MIT | optional local model runtime (planned, T07) |
| SaberOps worker adapters (portions) | Apache-2.0 | origin of the backend adapter pattern; attribution headers retained (T06) |

SaberOps is the origin of the backend adapter pattern and is licensed under
Apache-2.0. Attribution headers are kept where code is ported.
