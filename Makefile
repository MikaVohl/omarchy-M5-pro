PYTHON ?= python3

.PHONY: check
check:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B scripts/verify-release.py
	# Fixture paths must be physical: macOS TMPDIR lives under the /var -> /private/var
	# symlink, which the no-symlink guards under test correctly refuse.
	TMPDIR="$$(cd "$${TMPDIR:-/tmp}" && pwd -P)" PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -B -m unittest discover -s tests -p 'test_*.py' -v
