.PHONY: install up down status logs test metrics register list demo reload-crl

PYTHON ?= python3
DEVICE_ID ?= DEMO-0001
FAMILY ?= Demo
BOOTSTRAP_SECRET ?=

install:
	$(PYTHON) scripts/install_platform.py

up:
	$(PYTHON) scripts/start_platform.py

down:
	$(PYTHON) scripts/start_platform.py --stop-platform

status:
	docker compose ps

logs:
	docker compose logs -f api broker time-service simulator-manager

test:
	PYTHONPATH=server $(PYTHON) -m pytest -q

metrics:
	$(PYTHON) scripts/extract_metrics.py logs simulated_state --output metrics.csv --summary-output metrics-summary.csv

register:
	docker compose --profile tools run --rm tools scripts/admin.py register --device-id "$(DEVICE_ID)" --family "$(FAMILY)"

list:
	docker compose --profile tools run --rm tools scripts/admin.py list

demo:
	docker compose --profile tools run --rm tools scripts/demo_device.py --device-id "$(DEVICE_ID)" --family "$(FAMILY)" --bootstrap-secret "$(BOOTSTRAP_SECRET)" --api-url https://api:8443 --bootstrap-ca /pki/ca/ca.crt --mqtt-host broker

reload-crl:
	docker compose kill -s HUP broker
