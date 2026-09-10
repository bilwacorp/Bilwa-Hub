FROM python:3.12-slim

WORKDIR /app

# gcc/libpq-dev for asyncpg; iproute2/util-linux for the entrypoint's
# default-route fix (see docker-entrypoint.sh).
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev iproute2 util-linux \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Container starts as root (the entrypoint's route fix needs CAP_NET_ADMIN)
# and hands off to appuser via setpriv before running CMD.
RUN useradd -m appuser && chown -R appuser /app

COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
