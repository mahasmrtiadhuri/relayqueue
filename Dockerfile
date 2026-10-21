FROM python:3.12-slim
WORKDIR /app
RUN useradd --create-home app && mkdir /data && chown app:app /data
COPY --chown=app:app queue_store.py server.py worker.py index.html dashboard.js style.css ./
USER app
EXPOSE 8080
CMD ["python", "server.py", "--host", "0.0.0.0", "--db", "/data/relayqueue.db"]
