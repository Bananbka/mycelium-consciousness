# Build context: the repository root (see docker-compose.yaml).
FROM python:3.12-slim

RUN pip install --no-cache-dir boto3 msgpack && useradd --create-home app
WORKDIR /app

# the lab 8 function code, unchanged
COPY aws/lambda/handler.py aws/lambda/service.py aws/lambda/stores.py aws/lambda/inmemory.py ./
# the two modules the main project shares with it
COPY shared/src/shared/__init__.py shared/src/shared/backup_codec.py shared/src/shared/subscriptions.py shared/
# the HTTP wrapper, the provisioning job, CloudWatch metrics and fault injection
COPY aws/lb/server.py aws/lb/provision.py aws/lb/metrics.py aws/lb/chaos.py ./

USER app
ENV PYTHONUNBUFFERED=1 PORT=8080
EXPOSE 8080

# a failing dependency (DynamoDB/S3) makes /health answer 503 -> container "unhealthy"
HEALTHCHECK --interval=5s --timeout=4s --retries=3 --start-period=5s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/health', timeout=3)"

CMD ["python", "server.py"]
