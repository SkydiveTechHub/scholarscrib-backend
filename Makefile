PYTEST_PROCCESS_COUNT = 4

test-sample_app:
	pytest -n $(PYTEST_PROCCESS_COUNT) --cov=sample_app sample_app/tests --cov-report term-missing

test:
	pytest -n $(PYTEST_PROCCESS_COUNT) \
		--cov=users users/tests \
		--cov=social social/tests \
		--cov-report term-missing


integration-test:
	pytest -vv */tests/integration


dev-setup : 
	$ uv sync --locked
	$ pre-commit install     
	$ pre-commit install --hook-type commit-msg  


format : 
	$ ruff format

lint :
	$ ruff check --fix

type-check :
	$ pyright

format-check : format lint type-check