PYTHON ?= python3
HOST ?= 127.0.0.1
API_PORT ?= 8765
WEB_PORT ?= 5678
SERVICE := $(PYTHON) scripts/workbench_service.py

.PHONY: help start stop restart status logs stop-all \
	start-api stop-api restart-api status-api logs-api \
	start-web stop-web restart-web status-web logs-web \
	api-start api-stop api-restart api-status api-logs \
	web-start web-stop web-restart web-status web-logs \
	dev build validate

help:
	@printf '%s\n' \
		'Agent workbench commands:' \
		'  make start        Restart API and Web services as singletons' \
		'  make stop         Stop API and Web services' \
		'  make restart      Restart API and Web services' \
		'  make status       List managed workbench services' \
		'  make logs         Show API and Web logs' \
		'  make stop-all     Stop every managed workbench service' \
		'  make start-api    Restart API singleton only' \
		'  make stop-api     Stop API only' \
		'  make start-web    Restart Web singleton only' \
		'  make stop-web     Stop Web only' \
		'  make build        Build Web assets' \
		'  make validate     Run quick validation' \
		'' \
		'Overrides: HOST=127.0.0.1 API_PORT=8765 WEB_PORT=5678 PYTHON=python3'

start: start-api start-web

stop: stop-web stop-api

restart: restart-api restart-web

status:
	@$(SERVICE) list

logs:
	@$(SERVICE) logs --port $(API_PORT) || true
	@$(SERVICE) web-logs --port $(WEB_PORT) || true

stop-all:
	@$(SERVICE) stop-all

start-api:
	@$(SERVICE) start --host $(HOST) --port $(API_PORT)

stop-api:
	@$(SERVICE) stop --port $(API_PORT)

restart-api:
	@$(SERVICE) restart --host $(HOST) --port $(API_PORT)

status-api:
	@$(SERVICE) status --host $(HOST) --port $(API_PORT)

logs-api:
	@$(SERVICE) logs --port $(API_PORT)

start-web:
	@$(SERVICE) web-start --host $(HOST) --port $(WEB_PORT)

stop-web:
	@$(SERVICE) web-stop --port $(WEB_PORT)

restart-web:
	@$(SERVICE) web-restart --host $(HOST) --port $(WEB_PORT)

status-web:
	@$(SERVICE) web-status --host $(HOST) --port $(WEB_PORT)

logs-web:
	@$(SERVICE) web-logs --port $(WEB_PORT)

api-start: start-api
api-stop: stop-api
api-restart: restart-api
api-status: status-api
api-logs: logs-api

web-start: start-web
web-stop: stop-web
web-restart: restart-web
web-status: status-web
web-logs: logs-web

dev: start
	@printf 'API: http://$(HOST):$(API_PORT)\nWeb: http://$(HOST):$(WEB_PORT)\n'

build:
	@cd apps/web && npm run build

validate:
	@bash scripts/validate.sh --quick
