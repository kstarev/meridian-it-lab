# Every target runs offline on fake data, with no credentials.
# uv installs Python 3.13 and the exact versions in uv.lock;
# --locked stops with an error if uv.lock is out of date (ADR-012).
UV_RUN := uv run --locked
SEED ?= 1108
EVAL_SEED ?=
EVAL_MODEL ?= stub
EVAL_PUBLISH ?=
YEAR ?= 2027
POOL ?= tuning
COUNTRIES ?=

.DEFAULT_GOAL := help
.PHONY: help setup data access-data access-plan lint format test mutation eval demo holidays-preview review-pool images og site-demo search

help: ## Show the available targets
	@awk 'BEGIN {FS = ":.*## "} /^[a-z][a-z-]*:.*## / {printf "  make %-8s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

setup: ## Install Python 3.13 and the locked dependencies into .venv
	uv sync --locked

data: ## Generate the fake Meridian dataset into out/dataset (SEED=1108)
	$(UV_RUN) python -m meridian_dataset --seed $(SEED) --out out/dataset

access-data: ## Generate the Access-as-Code tuning world into out/access
	$(UV_RUN) python -m meridian_dataset.access --seed 6108 --out out/access

access-plan: ## Build the Access-as-Code plan for the tuning world into out/access/plan
	$(UV_RUN) python -m meridian_access.plan --world out/access/seed-6108 --out out/access/plan

lint: ## Check style and common bugs with ruff
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .

format: ## Auto-format the code and apply safe ruff fixes
	$(UV_RUN) ruff format .
	$(UV_RUN) ruff check --fix .

test: ## Run the tests (outbound network is blocked)
	$(UV_RUN) pytest

mutation: ## Run mutation testing on the safety code (slow; not part of make test or CI)
	$(UV_RUN) python tools/mutate.py run

eval: ## Run the tier0 eval (k=3): stub on the tuning seed, EVAL_MODEL=ollama on the held-out seed; fails on any unsafe action
	$(UV_RUN) python -m tier0.evals$(if $(EVAL_SEED), --seed $(EVAL_SEED),) --k 3 --model $(EVAL_MODEL) --out out/eval$(if $(EVAL_PUBLISH), --publish $(EVAL_PUBLISH),)

demo: ## Run the offline demo: six tickets through tier0, then the 3-day close
	$(UV_RUN) python -m tier0.demo --out out/demo

holidays-preview: ## Print a year's proactive-post schedule, nothing sent (YEAR=2027, SEED=1108 or COUNTRIES=all)
	$(UV_RUN) python -m tier0.proactive.preview --year $(YEAR) --seed $(SEED) $(if $(COUNTRIES),--countries $(COUNTRIES))

images: ## Regenerate the README's animated flow pictures in docs/img
	$(UV_RUN) python -m tier0.docs_img --out docs/img

site-demo: ## Rebuild the site's simulated-Slack demo, site/demo.html and its run log (stub only)
	$(UV_RUN) python -m tier0.site_demo --out site

og: ## Redraw the site's social preview, site/og.png (fetches Pillow and fontTools for this run only)
	$(UV_RUN) --with pillow --with fonttools --with brotli python -m tier0.site_og --out site/og.png

review-pool: ## Print a message pool's wording for human review (POOL=tuning, held_out_v3, ...)
	$(UV_RUN) python -m meridian_dataset.review $(POOL)

search: ## Search tracked files, leaving sealed paths out (make search q='pattern')
	@test -n "$(q)" || { echo "usage: make search q='pattern'" >&2; exit 2; }
	python3 tools/search.py "$(q)"
