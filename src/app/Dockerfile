FROM python:3.9-slim
ENV PYTHONUNBUFFERED=0

RUN mkdir -p /mini

WORKDIR /mini

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .

# Exposing Port
EXPOSE 5000

# Peer state persistence across container restarts
VOLUME ["/mini/state"]

# Default: orchestrated mode (monitoring.py sends /start_node)
# Set PRIOMON_STANDALONE=1 to run in standalone mode with seed discovery
ENV PRIOMON_STANDALONE=0
ENV PRIOMON_CONFIG=node_config.yaml
ENV PRIOMON_STATE_FILE=/mini/state/peer_state.json

# entrypoint handles both modes
CMD ["python", "./priomon.py"]