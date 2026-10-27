SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

UV ?= uv
PY := $(UV) run python
SPACY_MODEL := de_core_news_lg
SPACY_MODEL_VERSION := 3.8.0
SPACY_MODEL_URL := https://github.com/explosion/spacy-models/releases/download/$(SPACY_MODEL)-$(SPACY_MODEL_VERSION)/$(SPACY_MODEL)-$(SPACY_MODEL_VERSION)-py3-none-any.whl
GITLEAKS_VERSION := 8.30.1
GITLEAKS := $(HOME)/.local/bin/gitleaks

.PHONY: help setup test test-local lint reproduce live e2e page-data release-check \
	secrets secrets-install scan-forbidden

help:
	@echo "make setup           install dependencies and the pinned German spaCy model"
	@echo "make test            run the test suite (live and e2e markers excluded)"
	@echo "make test-local      prove the local filter path needs no network (sockets disabled)"
	@echo "make lint            ruff check, ruff format --check, mypy --strict on src"
	@echo "make reproduce       rebuild result files from stored responses (stub until T11)"
	@echo "make live            make real LLM calls (stub until T12; takes the live-llm lock)"
	@echo "make e2e             run the end-to-end check (stub until T13)"
	@echo "make page-data       export page JSON from results (stub until T12)"
	@echo "make release-check   local release gates: lint, test, forbidden scan, secrets"
	@echo "make secrets         run gitleaks over the working tree and git history"
	@echo "make secrets-install install pinned gitleaks $(GITLEAKS_VERSION) into ~/.local/bin"
	@echo "make scan-forbidden  scan for forbidden strings and private identifiers"

setup:
	$(UV) sync --all-groups
	$(UV) pip install --no-deps "$(SPACY_MODEL_URL)"

test:
	$(UV) run pytest -m "not live and not e2e"

test-local:
	$(UV) run pytest tests/test_local_only.py --disable-socket

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy --strict src

reproduce:
	@echo "reproduce: stub only; the deterministic rebuild lands in T11" >&2
	@exit 1

live:
	@echo "live: stub only; real LLM runs land in T12 (needs the live-llm lock)" >&2
	@exit 1

e2e:
	@echo "e2e: stub only; the n8n end-to-end run lands in T13" >&2
	@exit 1

page-data:
	@echo "page-data: stub only; export lands with T12/T14" >&2
	@exit 1

release-check: lint test test-local scan-forbidden secrets
	@if git ls-files --error-unmatch data/external >/dev/null 2>&1; then \
		echo "release-check: data/external is tracked; it must stay git-ignored" >&2; \
		exit 1; \
	fi
	@echo "release-check: local gates green (live, e2e, reproduce and page-data are still stubs)"

secrets-install:
	@mkdir -p "$(HOME)/.local/bin"
	@tmp="$$(mktemp)"; \
	curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v$(GITLEAKS_VERSION)/gitleaks_$(GITLEAKS_VERSION)_linux_x64.tar.gz" -o "$$tmp"; \
	tar -xzf "$$tmp" -C "$(HOME)/.local/bin" gitleaks; \
	rm -f "$$tmp"; \
	"$(GITLEAKS)" version

secrets:
	@if [ -x "$(GITLEAKS)" ]; then \
		"$(GITLEAKS)" dir . --redact --no-banner && \
		"$(GITLEAKS)" git . --redact --no-banner; \
	elif command -v gitleaks >/dev/null 2>&1; then \
		gitleaks dir . --redact --no-banner && \
		gitleaks git . --redact --no-banner; \
	elif command -v podman >/dev/null 2>&1; then \
		podman run --rm -v "$$PWD:/repo:ro" -w /repo \
			"ghcr.io/gitleaks/gitleaks:v$(GITLEAKS_VERSION)" dir . --redact --no-banner; \
	else \
		echo "secrets: gitleaks not found; run make secrets-install or install podman" >&2; \
		exit 1; \
	fi

scan-forbidden:
	@set -a; \
	if [ -f "$(HOME)/.config/ai-privacy-filter/private.env" ]; then \
		. "$(HOME)/.config/ai-privacy-filter/private.env"; \
	fi; \
	set +a; \
	$(PY) scripts/scan_forbidden.py
