# Rebuild every generated artifact from the raw data.
#
# Everything in report/ and README.md is produced by these targets. Nothing is written by
# hand, so no artifact can drift away from what the pipeline actually computed.

PY := .venv/bin/python
COOKIE := config/cookie_cats.yaml
CRITEO := config/criteo.yaml

.PHONY: all setup data test cookie criteo readme scorecards clean

all: test readme scorecards

setup:
	python3.12 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt

data:
	bash data/download.sh

test:
	$(PY) -m pytest -q

# --- Case Study 1 -------------------------------------------------------------
cookie:
	$(PY) -m readout.cli readout --config $(COOKIE)

readme:
	$(PY) -c "from analysis.peeking_sim import write_chart; \
	          from readout.run import run_experiment; \
	          write_chart(run_experiment('$(COOKIE)').peeking, 'report/peeking.png')"
	$(PY) -m readout.cli memo --config $(COOKIE) --out README.md

# --- Case Study 2 -------------------------------------------------------------
# Uses a file-backed database: the covariates table is ~168M rows, which is worth
# keeping on disk rather than rebuilding, and lets DuckDB spill instead of thrash.
criteo:
	$(PY) -m readout.cli readout --config $(CRITEO) --database criteo.duckdb

# --- Both scorecards, same generator, no edits (SPEC.md §7.9) -----------------
scorecards:
	$(PY) -m readout.cli html --config $(COOKIE) --out report/cookie_cats_scorecard.html
	$(PY) -m readout.cli html --config $(CRITEO) --database criteo.duckdb \
	      --out report/criteo_scorecard.html

clean:
	rm -f cookie_cats.duckdb criteo.duckdb criteo.duckdb.wal
	rm -rf .pytest_cache __pycache__ */__pycache__
