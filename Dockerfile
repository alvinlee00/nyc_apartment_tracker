FROM python:3.12-slim

WORKDIR /app

COPY requirements-api.txt .
RUN pip install --no-cache-dir -r requirements-api.txt

COPY api/ api/
COPY db.py models.py apartment_tracker.py renthop_scraper.py config.json ./
COPY data/ data/

CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
