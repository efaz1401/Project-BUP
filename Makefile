# GridWise Makefile
URL ?= http://127.0.0.1:8080

.PHONY: run verify test-harness test-paraphrase test-adversarial test-soak build docker-run

run:
	uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload

verify:
	python tools/harness.py --url $(URL)
	python tools/harness.py --url $(URL) --paraphrase
	python tools/harness.py --url $(URL) --adversarial
	python tools/harness.py --url $(URL) --soak

test-harness:
	python tools/harness.py --url $(URL)

test-paraphrase:
	python tools/harness.py --url $(URL) --paraphrase

test-adversarial:
	python tools/harness.py --url $(URL) --adversarial

test-soak:
	python tools/harness.py --url $(URL) --soak

build:
	docker build -t gridwise:latest .

docker-run:
	docker run -p 8080:8080 gridwise:latest
