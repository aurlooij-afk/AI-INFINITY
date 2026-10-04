FROM python:3.11-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 AI_INFINITY_DATA_DIR=/tmp/ai-infinity AI_INFINITY_3601_BACKGROUND=true
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY entrypoint_3700.py .
COPY ui_3700.html .
COPY backend_3700_01.part .
COPY backend_3700_02.part .
COPY backend_3700_03.part .
EXPOSE 10000
CMD ["python","entrypoint_3700.py"]
