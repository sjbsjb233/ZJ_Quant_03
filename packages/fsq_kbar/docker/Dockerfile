# 训练端容器镜像：CentOS Stream 9 + Python 3.12.4 + PyTorch 2.7.0 (CUDA 12.6)
# 与报告附录A实验环境一致：单机单卡RTX 4090/4090D(24GB)，BF16训练精度
FROM quay.io/centos/centos:stream9

# 系统依赖与Python 3.12
RUN dnf install -y python3.12 python3.12-pip python3.12-devel git \
    && dnf clean all \
    && ln -sf /usr/bin/python3.12 /usr/local/bin/python3 \
    && ln -sf /usr/bin/pip3.12 /usr/local/bin/pip3

WORKDIR /workspace/fsq-kbar-system

# 先安装依赖层，利用构建缓存（torch 从 CUDA 12.6 官方索引拉取）
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt \
    --extra-index-url https://download.pytorch.org/whl/cu126

# 拷贝代码
COPY . .

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/workspace/fsq-kbar-system

ENTRYPOINT ["bash", "docker/entrypoint.sh"]
CMD ["all"]
