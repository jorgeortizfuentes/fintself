# Makefile para Fintself

.DEFAULT_GOAL := help

install:
	@echo "Installing dependencies..."
	uv pip install -e .[dev]
	uv run pre-commit install

format:
	@echo "Formatting code with Ruff..."
	uv run ruff format .
	uv run ruff check . --fix

lint:
	@echo "Linting code with Ruff..."
	uv run ruff check .

install-hooks:
	@echo "Installing pre-commit hooks..."
	uv run pre-commit install

pre-commit:
	@echo "Running pre-commit hooks..."
	uv run pre-commit run --all-files

test:
	@echo "Running tests..."
	uv run pytest

clean:
	@echo "Cleaning up..."
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -delete
	rm -rf .pytest_cache .ruff_cache build dist *.egg-info

clean-debug:
	@echo "Cleaning old debug output (keeping only latest execution)..."
	@if [ -d "debug_output" ]; then \
		for bank_dir in debug_output/*/; do \
			if [ -d "$$bank_dir" ]; then \
				echo "Processing $$bank_dir"; \
				temp_file="/tmp/debug_files_$$$$"; \
				ls -t "$$bank_dir" | grep -E '^[0-9]{8}_[0-9]{6}_' > "$$temp_file"; \
				if [ -s "$$temp_file" ]; then \
					most_recent_file=$$(head -1 "$$temp_file"); \
					most_recent_time=$$(echo "$$most_recent_file" | cut -d'_' -f1-2); \
					echo "Most recent file: $$most_recent_file ($$most_recent_time)"; \
					cutoff_timestamp=$$(echo "$$most_recent_time" | sed 's/_//'); \
					keep_threshold=$$(expr $$cutoff_timestamp - 1000 2>/dev/null || echo $$cutoff_timestamp); \
					echo "Keeping files with timestamp >= $$keep_threshold"; \
					while read file; do \
						file_timestamp=$$(echo "$$file" | cut -d'_' -f1-2 | sed 's/_//'); \
						if [ $$file_timestamp -lt $$keep_threshold ]; then \
							echo "Deleting: $$file"; \
							rm -f "$$bank_dir/$$file"; \
						else \
							echo "Keeping: $$file"; \
						fi; \
					done < "$$temp_file"; \
				fi; \
				rm -f "$$temp_file"; \
			fi; \
		done; \
	else \
		echo "No debug_output directory found."; \
	fi

help:
	@echo "Available commands:"
	@echo "  install      - Instala las dependencias de producción y desarrollo."
	@echo "  format       - Formatea el código y arregla errores de linting automáticamente."
	@echo "  lint         - Revisa el código en busca de errores de estilo y programación."
	@echo "  install-hooks - Instala los hooks de pre-commit."
	@echo "  pre-commit   - Ejecuta los hooks de pre-commit en todos los archivos."
	@echo "  test         - Ejecuta la suite de pruebas."
	@echo "  clean        - Elimina archivos temporales y compilados."
	@echo "  clean-debug  - Elimina archivos de debug antiguos, mantiene solo la última sesión."
