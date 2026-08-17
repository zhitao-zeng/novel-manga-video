FROM python:3.12-slim-bookworm

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NOVEL_OUTPUT_ROOT=/output \
    NOVEL_UPLOAD_ROOT=/output/.uploads \
    NOVEL_JOB_ROOT=/output/.jobs

RUN sed -i 's@http://deb.debian.org@https://mirrors.tuna.tsinghua.edu.cn@g' /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg espeak-ng fonts-wqy-microhei ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY scripts ./scripts
RUN pip install .

RUN useradd --create-home --uid 10001 runner \
    && mkdir -p /input /output /models \
    && chown -R runner:runner /app /input /output
USER runner

EXPOSE 80

ENTRYPOINT ["uvicorn", "novel_manga.api:app", "--host", "0.0.0.0", "--port", "80"]
