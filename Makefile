PYTHON ?= python3
HOST ?= 127.0.0.1
API_PORT ?= 8765
WEB_PORT ?= 5173
SERVICE := $(PYTHON) scripts/workbench_service.py

.PHONY: help start stop restart status logs stop-all \
	api-start api-stop api-restart api-status api-logs \
	web-start web-stop web-restart web-status web-logs \
	dev build validate

help:
	@printf '%s\n' \
		'Agent workbench commands:' \
		'  make start        Start API and Web services' \
		'  make stop         Stop API and Web services' \
		'  make restart      Restart API and Web services' \
		'  make status       List managed workbench services' \
		'  make logs         Show API and Web logs' \
		'  make stop-all     Stop every managed workbench service' \
		'  make api-start    Start API only' \
		'  make api-stop     Stop API only' \
		'  make web-start    Start Web only' \
		'  make web-stop     Stop Web only' \
		'  make build        Build Web assets' \
		'  make validate     Run quick validation' \
		'' \
		'Overrides: HOST=127.0.0.1 API_PORT=8765 WEB_PORT=5173 PYTHON=python3'

start: api-start web-start

stop: web-stop api-stop

restart: api-restart web-restart

status:
	@$(SERVICE) list

logs:
	@$(SERVICE) logs --port $(API_PORT) || true
	@$(SERVICE) web-logs --port $(WEB_PORT) || true

stop-all:
	@$(SERVICE) stop-all --legacy-tmux

api-start:
	@$(SERVICE) start --host $(HOST) --port $(API_PORT)

api-stop:
	@$(SERVICE) stop --port $(API_PORT) --legacy-tmux

api-restart:
	@$(SERVICE) restart --host $(HOST) --port $(API_PORT) --replace-legacy-tmux

api-status:
	@$(SERVICE) status --host $(HOST) --port $(API_PORT)

api-logs:
	@$(SERVICE) logs --port $(API_PORT)

web-start:
	@$(SERVICE) web-start --host $(HOST) --port $(WEB_PORT)

web-stop:
	@$(SERVICE) web-stop --port $(WEB_PORT)

web-restart:
	@$(SERVICE) web-restart --host $(HOST) --port $(WEB_PORT)

web-status:
	@$(SERVICE) web-status --host $(HOST) --port $(WEB_PORT)

web-logs:
	@$(SERVICE) web-logs --port $(WEB_PORT)

dev: start
	@printf 'API: http://$(HOST):$(API_PORT)\nWeb: http://$(HOST):$(WEB_PORT)\n'

build:
	@cd apps/web && npm run build

validate:
	@bash scripts/validate.sh --quick
