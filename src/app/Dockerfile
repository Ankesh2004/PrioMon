FROM python:3.9-slim
ENV PYTHONUNBUFFERED=0

RUN mkdir -p /mini


WORKDIR /mini

COPY requirements.txt ./requirements.txt
RUN pip install --upgrade pip && pip install --no-cache-dir -r requirements.txt

COPY . .


# Exposing Port
EXPOSE 5000

# Persitance when needed
#VOLUME ["path/to/gossip-data"]

# Running Python App demon
CMD ["python", "./priomon.py"]