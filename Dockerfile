FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Preserve the complete TARGET-2050.199 engine
COPY main.py200 .

# Load TARGET-2050.200 compatibility/control layer
COPY main.py .

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
