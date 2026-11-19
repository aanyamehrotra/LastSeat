FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && useradd --create-home app
COPY app.py schema.sql ./
COPY templates ./templates
COPY static ./static
COPY migrations ./migrations
USER app
ENV PORT=8000
EXPOSE 8000
CMD ["sh", "-c", "exec gunicorn app:app --bind 0.0.0.0:${PORT} --workers 2 --threads 4 --timeout 30"]
