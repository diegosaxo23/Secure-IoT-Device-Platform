FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY server/requirements.txt /app/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/requirements.txt
COPY simulators /app/simulators
WORKDIR /app/simulators
EXPOSE 8090
CMD ["uvicorn", "manager:app", "--host", "0.0.0.0", "--port", "8090"]
