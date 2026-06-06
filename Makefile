.PHONY: install seed serve test gifs portal-gifs

install:
	pip install -r requirements.txt -r requirements-dev.txt

seed:
	python seed.py

serve:
	uvicorn main:app --port 8000 --reload

test:
	pytest test_api.py -v

# Regenerate the terminal-demo GIFs in docs/gifs/ (starts a temp server itself).
gifs:
	python scripts/make_gifs.py

# Regenerate the researcher-portal workflow GIFs (needs Playwright + Chromium).
portal-gifs:
	python scripts/make_portal_gifs.py
