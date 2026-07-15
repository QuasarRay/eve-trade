FROM python:3.13-slim@sha256:c33f0bc4364a6881bed1ec0cc2665e6c53c87a43e774aaeab88e6f17af105e4f

WORKDIR /app

COPY simulator/requirements.txt /app/requirements.txt
RUN --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    python -m pip install --retries 5 --timeout 60 -r /app/requirements.txt

RUN groupadd --gid 10001 simulator \
    && useradd --uid 10001 --gid simulator --create-home --home-dir /home/simulator simulator

COPY --chown=10001:10001 simulator /app

EXPOSE 8000

USER 10001:10001

CMD ["sh", "-c", "python manage.py migrate && python manage.py seed_trade_gui && python manage.py runserver 0.0.0.0:8000"]
