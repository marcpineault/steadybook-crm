FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD python -c "import db; db.init_db()" && flask --app dashboard:app run --host 0.0.0.0 --port ${PORT:-8080}
