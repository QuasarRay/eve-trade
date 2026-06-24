FROM python:3.13-slim

WORKDIR /app

COPY simulator/requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir -r /app/requirements.txt

COPY simulator /app

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate && python manage.py seed_trade_gui && python manage.py runserver 0.0.0.0:8000"]
