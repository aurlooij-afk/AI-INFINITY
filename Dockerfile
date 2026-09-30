FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 AI_INFINITY_DATA_DIR=/tmp/ai-infinity AI_INFINITY_PORTFOLIO_CYCLE_SECONDS=90
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY AI-Infinity-TARGET-2050.3601-COMPLETE-GENIUS-AI-ECOSYSTEM-main.py /app/main.py
EXPOSE 10000
CMD ["uvicorn","main:app","--host","0.0.0.0","--port","10000"]
