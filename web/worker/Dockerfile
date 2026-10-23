FROM python:3.13-slim-bookworm
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends bash iptables iproute2 nftables \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY gokaskad.sh /app/gokaskad.sh
COPY web/worker /app/web/worker
CMD ["python3", "/app/web/worker/server.py"]
