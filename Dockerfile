FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY data ./data
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 appuser && chown -R appuser:appuser /app
USER appuser

ENTRYPOINT ["signaljudge"]
CMD ["demo"]

