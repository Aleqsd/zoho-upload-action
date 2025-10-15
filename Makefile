.PHONY: test docker-build run-sample lint

test:
	python3 -m unittest discover -s tests -v

docker-build:
	docker build -t zoho-upload-action .

run-sample:
	docker run --rm --env-file .env -v "$$PWD":/workspace -w /workspace zoho-upload-action sample_upload.txt

lint:
	python3 -m compileall upload_zoho.py
