.PHONY: bootstrap api web dev demo demo-correction demo-expiry demo-permission-change verify benchmark openapi openapi-check clean

NODE24_BIN ?= /opt/homebrew/opt/node@24/bin
export PATH := $(NODE24_BIN):$(PATH)

bootstrap:
	uv sync --all-groups
	pnpm --dir web install
	pnpm --dir web exec playwright install chromium

api:
	uv run uvicorn lethe_control.app:create_app --factory --host 127.0.0.1 --port 8000

web:
	pnpm --dir web dev --host 127.0.0.1

dev:
	./scripts/dev.sh

demo:
	uv run lethe-control demo

demo-correction:
	./scripts/demo-correction.sh

demo-expiry:
	./scripts/demo-expiry.sh

demo-permission-change:
	./scripts/demo-permission-change.sh

verify: openapi-check
	uv run ruff format --check src tests
	uv run ruff check src tests
	uv run mypy src
	uv run pytest
	pnpm --dir web test -- --run
	pnpm --dir web build
	pnpm --dir web test:e2e

benchmark:
	uv run lethe-control benchmark

openapi:
	uv run lethe-control openapi --output web/openapi.json
	pnpm --dir web generate:api

openapi-check:
	@tmpdir="$$(mktemp -d)"; \
	trap 'rm -rf "$$tmpdir"' EXIT; \
	uv run lethe-control openapi --output "$$tmpdir/openapi.json"; \
	pnpm --dir web exec openapi-typescript "$$tmpdir/openapi.json" -o "$$tmpdir/schema.d.ts"; \
	if ! cmp -s web/openapi.json "$$tmpdir/openapi.json"; then \
		echo "OpenAPI drift detected. Run: make openapi"; \
		diff -u web/openapi.json "$$tmpdir/openapi.json" || true; \
		exit 1; \
	fi; \
	if ! cmp -s web/src/api/schema.d.ts "$$tmpdir/schema.d.ts"; then \
		echo "Generated dashboard type drift detected. Run: make openapi"; \
		diff -u web/src/api/schema.d.ts "$$tmpdir/schema.d.ts" || true; \
		exit 1; \
	fi

clean:
	rm -rf .lethe web/dist web/coverage web/playwright-report web/test-results
