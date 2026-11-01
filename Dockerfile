FROM python:3.13-slim
RUN useradd -u 10001 -m router
WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir .
USER 10001
ENV SOVEREIGN_STATE_DIR=/state
EXPOSE 8080
CMD ["uvicorn", "--factory", "sovereign_router.gateway:create_app", "--host", "0.0.0.0", "--port", "8080"]
