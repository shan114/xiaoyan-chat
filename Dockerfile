FROM python:3.11-slim

WORKDIR /app

# 安装编译依赖（sentence-transformers 需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

# 先装 PyTorch（最大的包，利用 Docker 层缓存）
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu

# 复制并安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY . .

# 暴露 Streamlit 端口
EXPOSE 8501

# 模型缓存放到持久化卷
ENV SENTENCE_TRANSFORMERS_HOME=/data/model_cache
ENV TRANSFORMERS_CACHE=/data/model_cache
ENV HF_ENDPOINT=https://hf-mirror.com

# Streamlit 配置
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0
ENV STREAMLIT_SERVER_HEADLESS=true
ENV STREAMLIT_BROWSER_GATHER_USAGE_STATS=false

# 数据目录指向 Fly.io 持久化卷
ENV DATA_DIR=/data

# 创建数据目录，启动时自动创建模型缓存目录
CMD mkdir -p /data/model_cache && \
    streamlit run web_fireboy.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true
