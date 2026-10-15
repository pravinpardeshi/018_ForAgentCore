FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# psycopg[binary] needs no libpq-dev; keep image small.
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY main.py ./
COPY src/ ./src/
COPY sql/ ./sql/

# AgentCore Runtime requires: non-root user + port 8080
RUN useradd -m -u 1000 bedrock_agentcore && chown -R bedrock_agentcore:bedrock_agentcore /app
USER bedrock_agentcore
EXPOSE 8080

CMD ["python", "main.py"]
