.PHONY: install test run-sample lint release

SHELL := /bin/bash

PYTHON := $(firstword $(strip $(shell command -v python 2>/dev/null) $(shell command -v python3 2>/dev/null)))
ifeq ($(PYTHON),)
$(error Python interpreter not found. Install python or python3 before running make targets.)
endif

pip = $(PYTHON) -m pip

VERSION ?= $(word 2,$(MAKECMDGOALS))
VERSION := $(strip $(VERSION))

ifneq ($(VERSION),)
ifneq ($(VERSION),release)
$(eval $(VERSION):;@:)
endif
endif

test:
	$(PYTHON) -m unittest discover -s tests -v

install:
	$(pip) install --require-virtualenv -r requirements.txt || $(pip) install -r requirements.txt

run-sample:
	set -euo pipefail; \
	if [[ ! -f .env ]]; then \
	  echo ".env file not found. Copy .env.example and populate credentials before running this target." >&2; \
	  exit 1; \
	fi; \
	set -o allexport; source .env; set +o allexport; \
	remote_name="sample-$$(date +%s).txt"; \
	"$(PYTHON)" upload_zoho.py sample_upload.txt --remote-name "$${remote_name}" --link-mode=both

lint:
	$(PYTHON) -m compileall upload_zoho.py

release:
	@if [ -z "$(VERSION)" ]; then \
	  echo "Usage: make release v1.0.2"; \
	  exit 1; \
	fi
	@echo "🚀 Releasing zoho-upload-action $(VERSION)..."
	git add .
	git commit -m "Release $(VERSION)" || echo "✅ No changes to commit."
	git push origin main
	# Update version tags
	git tag -fa v1 -m "v1 - latest stable"
	git tag -fa $(VERSION) -m "$(VERSION) release"
	git push origin v1 --force
	git push origin $(VERSION) --force
	@echo "✅ GitHub Action release updated: https://github.com/$(shell git config user.name)/$(shell basename `git rev-parse --show-toplevel`)/releases/tag/$(VERSION)"
