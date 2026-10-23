FROM python:3.13-slim-bookworm
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --chmod=644 web/api/server.py /app/server.py
USER 10001:10001
CMD ["python3", "/app/server.py"]
