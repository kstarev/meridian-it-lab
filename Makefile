# Every target runs offline on fake data, with no credentials.
# uv installs Python 3.13 and the exact versions in uv.lock;
# --locked stops with an error if uv.lock is out of date (ADR-012).
UV_RUN := uv run --locked
SEED ?= 1108
EVAL_SEED ?= 2611

.DEFAULT_GOAL := help
.PHONY: help setup data lint format test eval demo

help: ## Show the available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z][a-z-]*:.*## / {printf "  make %-8s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install Python 3.13 and the locked dependencies into .venv
	uv sync --locked

data: ## Generate the fake Meridian dataset into out/dataset (SEED=1108)
	$(UV_RUN) python -m meridian_dataset --seed $(SEED) --out out/dataset

lint: ## Check style and common bugs with ruff
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .

format: ## Auto-format the code and apply safe ruff fixes
	$(UV_RUN) ruff format .
	$(UV_RUN) ruff check --fix .

test: ## Run the tests (outbound network is blocked)
	$(UV_RUN) pytest

eval: ## Run the tier0 eval (k=3) on the held-out seed; fails on any unsafe action
	$(UV_RUN) python -m tier0.evals --seed $(EVAL_SEED) --k 3 --out out/eval

demo: ## Run the offline demo: six tickets through tier0, then the 3-day close
	$(UV_RUN) python -m tier0.demo --out out/demo
