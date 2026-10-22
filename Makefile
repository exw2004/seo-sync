# Same targets as every repo: test, lint, audit, check. `make check` is what CI runs.
# Tests need no network, database or credentials.
CONTAINER ?= podman
GITLEAKS_IMAGE := ghcr.io/gitleaks/gitleaks:v8.24.3
RUFF := ruff==0.17.1
PYTHON := 3.13
RUN = uv run --isolated --python $(PYTHON) --with-editable .

.PHONY: test lint audit check size

test:  ## Every test script, against this checkout installed as a package
	@for t in tests/test_*.py; do echo "== $$t"; $(RUN) python "$$t" || exit 1; done

lint:  ## ruff: syntax errors, unused names, likely bugs
	uvx --python $(PYTHON) $(RUFF) check --select E9,F,B .

audit:  ## Committed secrets, over the whole history
	$(CONTAINER) run --rm -v "$(CURDIR):/repo:ro" --security-opt label=disable \
		$(GITLEAKS_IMAGE) git /repo --no-banner --redact

# Files small enough for Claude to read whole (limits and baseline: the repo-checks README).
REPO_CHECKS := git+https://github.com/exw2004/repo-checks@v0.1.0

size:  ## No tracked file too big for Claude to read whole
	uvx --python 3.13 --from $(REPO_CHECKS) file-size-check

check: lint test size audit
