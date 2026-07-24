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
	uv run pytest -q
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
