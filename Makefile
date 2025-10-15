.PHONY: install test run-sample lint release

SHELL := /bin/bash

PYTHON := $(firstword $(strip $(shell command -v python 2>/dev/null) $(shell command -v python3 2>/dev/null)))
ifeq ($(PYTHON),)
$(error Python interpreter not found. Install python or python3 before running make targets.)
endif

pip = $(PYTHON) -m pip

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
	@echo "🚀 Releasing zoho-upload-action v1.0.1..."
	git add .
	git commit -m "🔖 Release v1.0.1" || echo "✅ No changes to commit."
	git push origin main
	# Update version tags
	git tag -fa v1 -m "v1 - latest stable"
	git tag -fa v1.0.1 -m "v1.0.1 release"
	git push origin v1 --force
	git push origin v1.0.1 --force
	@echo "✅ GitHub Action release updated: https://github.com/$(shell git config user.name)/$(shell basename `git rev-parse --show-toplevel`)/releases/tag/v1.0.1"
