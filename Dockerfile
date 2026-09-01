FROM python:3.11-slim

WORKDIR /app

COPY . /app

# Install Poetry and Python dependencies.
# Pure-Python: no external binaries required.
RUN set -eux;     apt-get update;     apt-get install -y --no-install-recommends git;     rm -rf /var/lib/apt/lists/*;     pip install --no-cache-dir poetry;     poetry install --no-dev --no-interaction --no-ansi

CMD ["poetry", "run", "python", "main.py"]
