.PHONY: test docker-build run-sample lint release

test:
	python3 -m unittest discover -s tests -v

docker-build:
	docker build -t zoho-upload-action .

run-sample:
	docker run --rm --env-file .env -v "$$PWD":/workspace -w /workspace zoho-upload-action sample_upload.txt

lint:
	python3 -m compileall upload_zoho.py

release:
	@echo "🚀 Releasing zoho-upload-action v1.0.0..."
	git add .
	git commit -m "🔖 Release v1.0.0" || echo "✅ No changes to commit."
	git push origin main
	# Update version tags
	git tag -fa v1 -m "v1 - latest stable"
	git tag -fa v1.0.0 -m "v1.0.0 release"
	git push origin v1 --force
	git push origin v1.0.0 --force
	@echo "✅ GitHub Action release updated: https://github.com/$(shell git config user.name)/$(shell basename `git rev-parse --show-toplevel`)/releases/tag/v1.0.0"
