FROM python:3.11-slim
WORKDIR /app

# OS deps minimal
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates tzdata \
    && rm -rf /var/lib/apt/lists/*

ENV TZ=UTC

# Python deps zuerst (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Code
COPY 06_live_bot ./06_live_bot
COPY 03_rules_engine ./03_rules_engine
COPY tests ./tests

WORKDIR /app/06_live_bot

# Bot startet im daemon-mode, wartet bis 12:27 CET = 06:27 ET, dann tradet
CMD ["python", "-u", "bot.py", "--daemon"]
