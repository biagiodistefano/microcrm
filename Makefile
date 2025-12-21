.PHONY: setup
setup:
	@test -f .env || cp .env.example .env
	uv sync --group dev
	uv run python src/manage.py migrate
	uv run python src/manage.py bootstrap
	uv run python src/manage.py seed

.PHONY: format
format:
	uv run ruff format .

.PHONY: lint
lint:
	uv run ruff check . --fix

.PHONY: mypy
mypy:
	uv run mypy --strict --extra-checks --warn-unreachable --warn-unused-ignores --no-incremental src

.PHONY: check
check: format lint mypy

.PHONY: test
test:
	uv run pytest --cov=src --cov-report=term --cov-report=html -v src/

.PHONY: test-failed
test-failed:
	uv run pytest --cov=src --cov-report=term --cov-report=html -v --last-failed src/

.PHONY: run
run:
	uv run python src/manage.py runserver

.PHONY: shell
shell:
	uv run python src/manage.py shell

.PHONY: migrations
migrations:
	uv run python src/manage.py makemigrations

.PHONY: migrate
migrate:
	uv run python src/manage.py migrate

.PHONY: bootstrap
bootstrap:
	uv run python src/manage.py bootstrap

.PHONY: seed
seed:
	uv run python src/manage.py seed

.PHONY: run-celery
run-celery:
	cd src && uv run celery -A crm worker -l INFO

.PHONY: run-celery-beat
run-celery-beat:
	cd src && uv run celery -A crm beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler

.PHONY: nuke-db
nuke-db:
	@read -p "Are you sure you want to nuke the database? Type 'yes' to continue: " confirm && if [ "$$confirm" = "yes" ]; then \
		rm -f src/db.sqlite3; \
		rm -rf src/leads/migrations/0*.py; \
		uv run python src/manage.py makemigrations; \
		uv run python src/manage.py migrate; \
		uv run python src/manage.py bootstrap; \
	else \
		echo "Nuke database aborted."; \
	fi

.PHONY: release
release:
	@VERSION=$$(grep '^version = ' pyproject.toml | sed 's/version = "\(.*\)"/\1/'); \
	echo "Current version: $$VERSION"; \
	read -p "Do you want to create a release v$$VERSION? (y/n): " confirm && if [ "$$confirm" = "y" ]; then \
		gh release create "v$$VERSION" --generate-notes; \
	else \
		echo "Release aborted."; \
	fi
