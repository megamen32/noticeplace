ARG PYTHON_IMAGE=python:3.12.15-slim-bookworm@sha256:34386ef0cb081344d7ec1c103ba398e6e9f64e9ab3a1509accc92a4e24a07258
FROM ${PYTHON_IMAGE}
ARG SOURCE_REVISION
LABEL org.opencontainers.image.title="NoticePlace API" \
      org.opencontainers.image.revision="${SOURCE_REVISION}"
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /app
COPY bin/noticeplace-api /app/bin/noticeplace-api
COPY notification_center /app/notification_center
COPY mcp /app/mcp
COPY deploy/container/healthcheck.py /app/healthcheck.py
RUN test -n "$SOURCE_REVISION" && printf '%s\n' "$SOURCE_REVISION" > /app/.source-revision
USER 65532:65532
EXPOSE 8091
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD ["python", "/app/healthcheck.py"]
ENTRYPOINT ["python", "/app/bin/noticeplace-api"]
