PYTHON ?= python3

.PHONY: check
check:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B scripts/verify-release.py
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B -m unittest discover -s tests -p 'test_*.py' -v
