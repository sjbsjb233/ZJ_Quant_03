# =====================================================================
# 金融K线基座大模型量化投资系统 —— 统一运行环境
# 口径与报告附录A附表1一致：
#   CentOS Stream 9（Docker容器） + 单张 NVIDIA RTX 4090/4090D (24GB)
#   Python 3.12 / PyTorch 2.7.0 / NumPy 1.26.2 / Pandas 2.2.2 / Qlib
#
# 说明：容器内不安装完整 CUDA Toolkit —— PyTorch 2.7.0 的 cu128 轮子
# 自带全部 CUDA 用户态运行库；宿主机只需安装 NVIDIA 驱动与
# nvidia-container-toolkit，由容器运行时注入设备。
# =====================================================================
FROM quay.io/centos/centos:stream9

ENV LANG=C.UTF-8 \
    TZ=Asia/Shanghai \
    PIP_NO_CACHE_DIR=1

RUN dnf -y update && \
    dnf -y install python3.12 python3.12-pip python3.12-devel \
                   git wget which gcc gcc-c++ make && \
    dnf clean all && \
    alternatives --install /usr/bin/python3 python3 /usr/bin/python3.12 1

# PyTorch 2.7.0（BF16 / SDPA / FlashAttention 后端），CUDA 12.8 轮子
RUN python3 -m pip install --upgrade pip && \
    python3 -m pip install torch==2.7.0 --index-url https://download.pytorch.org/whl/cu128

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install -r /tmp/requirements.txt

WORKDIR /workspace
COPY . /workspace
ENV PYTHONPATH=/workspace

ENTRYPOINT ["/workspace/docker/entrypoint.sh"]
CMD ["bash"]
