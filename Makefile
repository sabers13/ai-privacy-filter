SHELL := /usr/bin/env bash
.DEFAULT_GOAL := help

UV ?= uv
PY := $(UV) run python
SPACY_MODEL := de_core_news_lg
SPACY_MODEL_VERSION := 3.8.0
SPACY_MODEL_URL := https://github.com/explosion/spacy-models/releases/download/$(SPACY_MODEL)-$(SPACY_MODEL_VERSION)/$(SPACY_MODEL)-$(SPACY_MODEL_VERSION)-py3-none-any.whl
GITLEAKS_VERSION := 8.30.1
GITLEAKS := $(HOME)/.local/bin/gitleaks

.PHONY: help setup test test-local coverage lint reproduce live e2e demo demo-down page-data \
	release-check clean-clone secrets secrets-install scan-forbidden

help:
	@echo "make setup           install dependencies and the pinned German spaCy model"
	@echo "make test            run the test suite (live and e2e markers excluded)"
	@echo "make test-local      prove the local filter path needs no network (sockets disabled)"
	@echo "make coverage        run the tests with the 85% branch-coverage floor on src"
	@echo "make lint            ruff check, ruff format --check, mypy --strict on src"
	@echo "make reproduce       rebuild detection results and triage metrics from stored data"
	@echo "make live            make real LLM calls (takes the live-llm lock; set AIPF_LOCK_DIR)"
	@echo "make e2e             run the n8n end-to-end check (compose, live-LLM lock)"
	@echo "make demo            start the n8n review stack and post three enquiries, leave it up"
	@echo "make demo-down       stop the demo stack and delete .n8n-data"
	@echo "make page-data       rebuild the results/page bundle from the committed results"
	@echo "make release-check   local release gates: lint, test, forbidden scan, secrets"
	@echo "make clean-clone     clone locally and prove setup/test/reproduce from a clean tree"
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

coverage:
	$(UV) run pytest -m "not live and not e2e" \
		--cov=ai_privacy_filter --cov-report=term-missing --cov-fail-under=85

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .
	$(UV) run mypy --strict src

reproduce:
	$(PY) -m eval.detection --reproduce
	$(PY) scripts/reproduce.py

live:
	$(PY) scripts/run_triage.py --live

e2e:
	scripts/e2e/run.sh

demo:
	scripts/demo/run.sh up

demo-down:
	scripts/demo/run.sh down

page-data:
	$(PY) scripts/page_export.py

release-check: lint test test-local scan-forbidden secrets
	@if git ls-files --error-unmatch data/external >/dev/null 2>&1; then \
		echo "release-check: data/external is tracked; it must stay git-ignored" >&2; \
		exit 1; \
	fi
	@echo "release-check: local gates green (live and e2e need the private environment)"

clean-clone:
	scripts/clean_clone_check.sh

secrets-install:
	@mkdir -p "$(HOME)/.local/bin"
	@tmp="$$(mktemp)"; \
	curl -sSfL "https://github.com/gitleaks/gitleaks/releases/download/v$(GITLEAKS_VERSION)/gitleaks_$(GITLEAKS_VERSION)_linux_x64.tar.gz" -o "$$tmp"; \
	tar -xzf "$$tmp" -C "$(HOME)/.local/bin" gitleaks; \
	rm -f "$$tmp"; \
	"$(GITLEAKS)" version

secrets:
	@if [ -x "$(GITLEAKS)" ]; then \
		"$(GITLEAKS)" dir . --config .gitleaks.toml --redact --no-banner && \
		"$(GITLEAKS)" git . --config .gitleaks.toml --redact --no-banner; \
	elif command -v gitleaks >/dev/null 2>&1; then \
		gitleaks dir . --config .gitleaks.toml --redact --no-banner && \
		gitleaks git . --config .gitleaks.toml --redact --no-banner; \
	elif command -v podman >/dev/null 2>&1; then \
		podman run --rm -v "$$PWD:/repo:ro" -w /repo \
			"ghcr.io/gitleaks/gitleaks:v$(GITLEAKS_VERSION)" dir . --config .gitleaks.toml --redact --no-banner; \
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
