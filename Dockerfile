FROM python:3.11-slim

WORKDIR /app

# 允许构建时切换镜像源：默认官方 PyPI（清华源对部分新版本 wheel 返回 403）
ARG PIP_INDEX_URL=https://pypi.org/simple

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt \
    --index-url ${PIP_INDEX_URL} --timeout 60 --retries 3

COPY . .

CMD ["python", "-m", "pytest", "-q"]
