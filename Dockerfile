# 基础镜像：阿里云 ACR VPC 内网（已预装常用工具）
FROM acr-openxlab-prod-registry-vpc.cn-shanghai.cr.aliyuncs.com/public/ubuntu:22.04-python3.10

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends python3-pip && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --no-cache-dir --no-compile -r requirements.txt

COPY . .

VOLUME ["/app/data"]

ENV DATABASE_PATH=/app/data/paper_pub.db

# 业务端口 8000 + Prometheus 指标端口 9091
EXPOSE 8000 9091

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python3", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
