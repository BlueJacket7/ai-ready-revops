PY   ?= python3
SEED ?= 42
# gate needs PRIVACY_KEY; for local runs use: make all DEV=1
GATE_FLAGS := $(if $(filter 1,$(DEV)),--dev,)

.PHONY: all generate gate scan dbt test clean

all: generate gate dbt scan test

generate:
	$(PY) generator/generate.py --seed $(SEED) --out data/raw

gate:
	$(PY) privacy/gate.py $(GATE_FLAGS)

scan:
	$(PY) privacy/scan.py

dbt:
	cd dbt && DBT_PROFILES_DIR=. dbt build

test:
	$(PY) -m pytest -q tests

clean:
	rm -rf data/raw data/clean data/llm data/warehouse.duckdb dbt/target dbt/logs
