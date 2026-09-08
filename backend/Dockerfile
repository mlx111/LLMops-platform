# LLMOps 平台后端镜像
# 国内构建可覆盖依赖源：docker build --build-arg PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
# 基础镜像显式走已验证的国内镜像站（如在海外构建，可改回 python:3.11-slim）
FROM docker.1ms.run/library/python:3.11-slim

ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# 先装依赖（利用 Docker 层缓存；requirements.txt 未变动时不重装）
COPY pyproject.toml requirements.txt ./
RUN pip install --no-cache-dir --default-timeout=300 --retries 5 -r requirements.txt

# 再复制业务代码并以可编辑模式安装（补齐 pydantic-settings 等 pyproject 依赖）
COPY app ./app
COPY migrations ./migrations
COPY alembic.ini ./
RUN pip install --no-cache-dir -e .

# SQLite 数据目录（compose 中挂载 volume 持久化）
RUN mkdir -p /app/data

EXPOSE 8000

# 表结构由 startup 的 create_all + seed 自动初始化，无需手动迁移
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
