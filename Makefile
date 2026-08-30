SHELL := /bin/zsh
.DEFAULT_GOAL := help

help: ## Show available targets
	@python -c "from pathlib import Path; [print(line.split(': ## ')[0].ljust(18), line.split(': ## ')[1]) for line in Path('Makefile').read_text().splitlines() if ': ## ' in line]"

install-dev: ## Install editable development CLI with uv
	@uv tool install --editable . --force

test: ## Run the complete test suite
	@uv run pytest -v

lint: ## Run linting and formatting verification
	@uv run ruff check src tests
	@uv run ruff format --check src tests

typecheck: ## Run strict static type checks
	@uv run pyright

smoke: ## Start an isolated daemon and prove a CLI Unix-socket roundtrip
	@uv run browser-proxy --help
	@uv run browser-proxy do --help
	@state="$$(mktemp -d)"; export BROWSER_PROXY_STATE_DIR="$$state" BROWSER_PROXY_EXTENSION_PORT=39491; uv run browser-proxy daemon >/dev/null 2>&1 & pid=$$!; for attempt in {1..50}; do [[ -S "$$state/browser-proxy.sock" ]] && break; sleep 0.1; done; uv run browser-proxy do profile-list '{}'; uv run browser-proxy admin stop >/dev/null; wait $$pid; rmdir "$$state"

check: lint typecheck test smoke ## Run all static checks, tests, and smoke checks

stress: ## Exercise concurrent daemon lock ownership in an isolated runtime directory
	@uv run pytest -v tests/test_contract.py

build: ## Build the Python distribution
	@uv build --clear

git-push: ## Push the current branch to both remotes
	@branch="$$(git branch --show-current)"; git push github "$$branch"; git push gitlab "$$branch"

push: git-push ## Push to GitHub and GitLab

release: check build push ## Verify build and push the release
