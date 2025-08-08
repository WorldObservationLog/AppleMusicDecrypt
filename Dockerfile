FROM python:3.12

RUN apt-get update && \
    apt-get install -y bash curl && \
    rm -rf /var/lib/apt/lists/*

RUN curl -sSL https://install.python-poetry.org | python3 -

WORKDIR /app

COPY . /app

ENV PATH="/root/.local/bin:$PATH"

RUN bash ./tools/install-deps.sh && \
    poetry install

CMD ["poetry", "run", "python", "main.py"]