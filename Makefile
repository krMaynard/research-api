.PHONY: install seed serve mcp test lint typecheck gifs portal-gifs

install:
	pip install -r requirements.txt -r requirements-dev.txt

seed:
	python seed.py

serve:
	uvicorn main:app --port 8000 --reload

# Native MCP stdio server (talks to a running API — start `make serve` first).
# Installs into its own venv so the MCP SDK's deps don't touch the app's pins.
mcp:
	python -m venv .venv-mcp && . .venv-mcp/bin/activate && \
		pip install -q -r requirements-mcp.txt && python mcp_server.py

test:
	pytest test_api.py test_mcp_server.py -v

# Same lint CI runs on every PR/push.
lint:
	python -m pyflakes main.py seed.py seed_harmonised.py demo.py conftest.py mcp_server.py test_api.py test_mcp_server.py

# Static type check (config in mypy.ini); also runs in CI.
typecheck:
	python -m mypy

# Regenerate the terminal-demo GIFs in docs/gifs/ (starts a temp server itself).
gifs:
	python scripts/make_gifs.py

# Regenerate the researcher-portal workflow GIFs (needs Playwright + Chromium).
portal-gifs:
	python scripts/make_portal_gifs.py
