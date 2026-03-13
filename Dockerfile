# 基础镜像
FROM python:3.8-slim

# 设置工作目录
WORKDIR /app

# 【修复时区】设置上海时区
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 禁用Python缓冲/字节码
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# 安装系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    python3-dev \
    && rm -rf /var/lib/apt/lists/*

# 安装Python依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制代码
COPY app.py .
COPY api_server.py .

# 创建静态文件目录
RUN mkdir -p /app/static

# 复制静态文件
COPY static/ /app/static/

# 暴露 Web 管理界面端口
EXPOSE 8000

# 启动命令
CMD ["python", "app.py"]
