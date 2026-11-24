# 后台镜像：官方 Python 轻量版
#
# 这个 Dockerfile 只负责"把后台跑起来"，不装任何业务系统的客户端，
# 也不在里面写任何监控地址、账号、网段 —— 那些一律走环境变量 / 配置页。
FROM python:3.13-slim

# PYTHONUNBUFFERED：让日志实时打到容器输出，方便出问题时排查；
# PYTHONDONTWRITEBYTECODE：不在容器里生成 __pycache__，镜像干净点；
# APP_ENV：测试镜像默认测试环境；生产镜像由 Dockerfile.prod 固定 production。
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    APP_ENV=test

WORKDIR /srv/backend

# 先只拷依赖清单、先装依赖：以后改业务代码时，Docker 能复用这一层缓存，
# 不用每次重新 pip install（内网环境这一步很省时间）。
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷源码
COPY app/ ./app/

# 只暴露一个端口，具体映射到宿主机哪个端口由 compose 从 .env 读，不在这里写死
EXPOSE 8000

# uvicorn 是 FastAPI 的服务器；--host 0.0.0.0 只是"容器内所有网卡都监听"，
# 好让容器外面能访问，不是说要去连什么外部系统。
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
