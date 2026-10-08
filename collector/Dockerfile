FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY collector ./collector
RUN useradd -m app
USER app
CMD ["python", "-m", "collector.main"]
