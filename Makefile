SHELL := /usr/bin/env bash
.DEFAULT_GOAL := test

.PHONY: bootstrap format lint typecheck test build run accept-local accept-tools accept-brev

bootstrap:
	bash scripts/build_patinae_viewer.sh
	corepack enable
	pnpm install --frozen-lockfile
	uv sync --all-packages --all-extras --all-groups
	rustup target add wasm32-unknown-unknown

format:
	uv run ruff format packages providers tests
	cargo fmt --all
	pnpm --dir packages/web exec tsc --noEmit

lint:
	uv run ruff check packages providers tests
	cargo clippy --workspace --all-targets -- -D warnings
	bash -n run.sh deploy/brev/*.sh deploy/local/*.sh scripts/*.sh

typecheck:
	bash scripts/generate_api_types.sh
	pnpm typecheck

test:
	cargo test --workspace --locked
	@rfd4_root="$${ANNOTATION_STUDIO_RFD4_ROOT:-$${RFPROTEINA_ROOT:-$(CURDIR)/../RFD4-Proteina-dev}}"; \
	if [[ ! -d "$${rfd4_root}/src/rfproteina" ]]; then \
		echo "RFD4 checkout not found: $${rfd4_root}" >&2; \
		echo "Set ANNOTATION_STUDIO_RFD4_ROOT or place RFD4-Proteina-dev beside this repository." >&2; \
		exit 1; \
	fi; \
	PYTHONPATH="$${rfd4_root}/src$${PYTHONPATH:+:$${PYTHONPATH}}" \
		uv run python scripts/run_pytest.py -q
	pnpm test

build:
	pnpm build
	uv build --all-packages
	cargo build --workspace --release --locked

run:
	bash run.sh

accept-local:
	bash scripts/accept_local.sh

accept-tools:
	bash scripts/accept_tools.sh

accept-brev:
	bash scripts/accept_brev.sh
