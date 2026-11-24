# 任务 worker 镜像（队列消费者 + AgentScope 执行器）。
#
# 多阶段：builder 装依赖，runtime 只带 venv 和运行代码。
#
# worker 不处理截图 OCR：截图在 API 进程里识别完，队列里只传 ocr_text。
# 所以这里从 backend/requirements.txt 过滤掉 rapidocr，避免连带装
# opencv-python-headless（约 140 MB）。onnxruntime / numpy 仍保留，
# 因为知识治理的本地 embedding 要用。
#
# 构建上下文是仓库根（见 docker-compose.build.yml 的 context: .）。

# 国内构建默认走镜像站；能直连 Docker Hub 时用
#   --build-arg PYTHON_IMAGE=python:3.13-slim
# 覆盖。
ARG PYTHON_IMAGE=docker.1ms.run/library/python:3.13-slim

FROM ${PYTHON_IMAGE} AS builder

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/

ENV PIP_DEFAULT_TIMEOUT=60 \
    PIP_RETRIES=5 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements.txt ./backend/requirements.txt
COPY worker/requirements.txt ./worker/requirements.txt
RUN grep -v '^rapidocr' backend/requirements.txt > /tmp/backend-runtime.txt \
 && pip install --no-cache-dir --no-compile \
      --index-url "${PIP_INDEX_URL}" \
      -r /tmp/backend-runtime.txt -r worker/requirements.txt \
 && find /opt/venv -type d -name __pycache__ -prune -exec rm -rf {} +

FROM ${PYTHON_IMAGE} AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/srv/code/backend:/srv/code/worker:/srv/code/packages

RUN apt-get update \
 && apt-get install -y --no-install-recommends libgomp1 \
 && rm -rf /var/lib/apt/lists/* \
 && groupadd --system ops \
 && useradd --system --gid ops --home-dir /srv/code --shell /usr/sbin/nologin ops

WORKDIR /srv/code

COPY --from=builder /opt/venv /opt/venv
COPY --chown=ops:ops backend/app ./backend/app
COPY --chown=ops:ops backend/scripts ./backend/scripts
COPY --chown=ops:ops backend/migrations ./backend/migrations
COPY --chown=ops:ops worker ./worker
COPY --chown=ops:ops packages ./packages

RUN mkdir -p /srv/code/var && chown -R ops:ops /srv/code/var

USER ops

CMD ["python", "-u", "backend/scripts/run_queue_worker.py", "--interval", "5"]
