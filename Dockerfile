FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD python -c "import db; db.init_db()" && gunicorn --bind 0.0.0.0:${PORT:-8080} --workers 2 dashboard:app
