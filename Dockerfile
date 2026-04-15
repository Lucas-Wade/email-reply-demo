FROM python:3.11-slim

WORKDIR /app

# 系统依赖：CJK 字体（报价单 PDF）+ curl（healthcheck）
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-cjk \
    curl \
 && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（先 COPY 避免每次重建）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码（.env 不进镜像，由 env_file 挂载）
COPY . .

# 数据目录（挂载点）
RUN mkdir -p /data /app/logs

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
