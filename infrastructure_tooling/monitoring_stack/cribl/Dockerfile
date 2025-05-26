### ---- STAGE 1: dev, install deps into venv ----
FROM cgr.dev/chainguard/python:latest-dev AS dev

WORKDIR /app

RUN python -m venv venv
ENV PATH="/app/venv/bin":$PATH

# install dependencies
COPY requirements.txt requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

### ---- STAGE 2: runtime ----
FROM cgr.dev/chainguard/python:latest

WORKDIR /app

COPY sample_dev_logs.py sample_dev_logs.py
COPY .env .
COPY --from=dev /app/venv /app/venv
ENV PATH="/app/venv/bin":$PATH


ENTRYPOINT ["python", "sample_dev_logs.py"]
