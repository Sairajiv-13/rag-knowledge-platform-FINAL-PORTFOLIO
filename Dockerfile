# ---- builder: install the package + deps into an isolated venv ----
FROM python:3.12-slim AS builder
WORKDIR /app
ENV PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml README.md ./
COPY src ./src
# CPU-only torch first: the default PyPI wheel bundles CUDA (multi-GB); the
# cpu index wheel is ~5x smaller and this service does CPU inference only.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
 && /opt/venv/bin/pip install --no-cache-dir ".[local-inference]"

# ---- runtime: no compilers, no pip cache, non-root user ----
FROM python:3.12-slim
RUN useradd --create-home appuser
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
WORKDIR /app
COPY alembic.ini ./
COPY migrations ./migrations
COPY start.sh start-worker.sh ./
RUN chmod +x start.sh start-worker.sh && chown appuser:appuser start.sh start-worker.sh
USER appuser
EXPOSE 8000
# Default command runs migrations then the API on $PORT (Railway-friendly).
# The worker service overrides this with: ./start-worker.sh
CMD ["./start.sh"]
