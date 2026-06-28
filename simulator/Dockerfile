FROM python:3.13-slim

WORKDIR /app

COPY simulator/requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --retries 5 --timeout 60 -r /app/requirements.txt

COPY simulator /app

EXPOSE 8000

CMD ["sh", "-c", "python manage.py migrate && python manage.py seed_trade_gui && python manage.py runserver 0.0.0.0:8000"]
