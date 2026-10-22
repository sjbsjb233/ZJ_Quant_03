<div align="center">

<img src="assets/readme/banner.svg" alt="FSQ K 线基础模型 · ZJ Quant" width="100%">

<p><a href="README.md">English</a> &nbsp;|&nbsp; <b>简体中文</b></p>

<a href="#实验结果"><img src="https://img.shields.io/badge/OOS_excess_return-17.8%25_·_IR_1.42-3B5BDB?style=flat-square" alt="样本外"></a>
<img src="https://img.shields.io/badge/model-41M_decoder--only_Transformer-3B5BDB?style=flat-square" alt="模型">
<img src="https://img.shields.io/badge/market-China_A--shares-12B886?style=flat-square" alt="市场">
<img src="https://img.shields.io/badge/PyTorch-2.7-EE4C2C?style=flat-square&logo=pytorch&logoColor=white" alt="PyTorch">
<img src="https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/deploy-Docker_Compose-2496ED?style=flat-square&logo=docker&logoColor=white" alt="Docker">

<br><br>

<a href="#快速开始"><b>快速开始</b></a> &nbsp;·&nbsp;
<a href="#概览"><b>概览</b></a> &nbsp;·&nbsp;
<a href="#一分钟看懂"><b>核心思路</b></a> &nbsp;·&nbsp;
<a href="#系统架构"><b>架构</b></a> &nbsp;·&nbsp;
<a href="#fsq-模型详解"><b>模型</b></a> &nbsp;·&nbsp;
<a href="#实验结果"><b>结果</b></a> &nbsp;·&nbsp;
<a href="#风险管理"><b>风控</b></a> &nbsp;·&nbsp;
<a href="#研究平台"><b>平台</b></a> &nbsp;·&nbsp;
<a href="#安装指南"><b>安装指南</b></a>

</div>

<br>

> **FSQ** 是一个在中国 A 股 K 线数据上从零预训练的基础模型。它用*有限标量量化*（Finite Scalar Quantization）把每根日 K 线变成一个离散 token，
> 学习 token 之间的先后规律，并在一次前向中给出未来 10 日收益的**区间**预测。
> 预测结果被构造成选股因子，在 **ZJ Quant** 中完成评估、风控与交易。ZJ Quant 是一个容器化的多因子研究平台，
> 包含数据层、因子引擎、研究控制台和可观测层。

## 概览

本仓库包含两套并行运行的系统，二者共用同一套数据与因子契约。

| | 是什么 | 位置 |
| :-- | :-- | :-- |
| 🧠 **FSQ 引擎** *（核心）* | K 线 tokenizer、自回归基础模型、分位数预测头、风险调整因子、组合构建与风控，以及 Qlib 和 SuperMind 回测 | [`packages/fsq_kbar`](packages/fsq_kbar) |
| 🏗️ **ZJ Quant 平台** | 受治理的时点一致（point-in-time）数据湖、因子库与评估引擎、研究控制台、可观测层。FSQ 以两个一等因子的身份接入 | [`apps/`](apps) · [`packages/quant_*`](packages) |

<img src="assets/readme/kpi.svg" alt="样本外核心指标" width="100%">

<sub>严格样本外：模型从未见过 2025-06-30 之后的数据，测试区间为 2025-07-01 至 2026-06-18（235 个交易日）。收益已扣除 0.15 % 单边交易成本，基准为沪深 300 与中证 800 指数。完整实验协议见<a href="#实验结果">实验结果</a>。</sub>

## 快速开始

大约 15 分钟即可用演示数据跑起平台。需要 Docker（含 Compose v2）和 8 GB 内存。Linux 和 macOS 可以直接运行；Windows 请在 WSL 2 终端中操作。不需要 GPU。

```bash
git clone https://github.com/sjbsjb233/ZJ_Quant_03.git
cd ZJ_Quant_03/deploy
cp .env.example .env        # 然后编辑 .env：WEB_PORT=8080、ENABLE_SCHEDULER=false，
                            # 并为 JWT_SECRET 和 LOG_INGEST_TOKEN 填入较长的随机字符串
export COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml

docker compose build
docker compose run --rm data-keeper python -m keeper_service.seed_demo
docker compose run --rm web-backend python -m quant_web.seed_users
docker compose up -d
```

打开 <http://localhost:8080>，用 `admin` / `admin123` 登录。
PowerShell 用法、GPU 训练 FSQ、本地开发和常见问题，见[安装指南](#安装指南)。

## 一分钟看懂

<img src="assets/readme/idea.svg" alt="读图、预测、决策" width="100%">

大多数量化选股信号是人工写出的公式，比如"20 日动量"或"市净率"，只捕捉了价格图里很小一部分信息。
通用时间序列基础模型能自动学习规律，但训练数据中金融数据不到约 1 %，迁移到噪声大、尾部厚的股价上效果不佳。

FSQ 把 K 线当成一种语言。它先学出一套紧凑的 K 线形态"词表"，再在 2005 年以来全部沪深 A 股上学习 K 线前后衔接的"语法"。
对每只股票的每个交易日，它给出**悲观、典型、乐观**三种 10 日结果。典型结果高**且**区间窄的股票排在最前，因为模型对它们既看好又有把握。
系统的其余部分负责决定持有多少、何时踩刹车。

### 亮点

- **金融原生预训练。** 语料 100 % 来自金融数据：7 种频率（1 分钟到周线）共 30.6 亿根原始 K 线，并纳入 5,872 只退市股以避免幸存者偏差。
- **FSQ 并行分词。** 4 个有界标量、档位 `[8, 8, 8, 5]`，共 2,560 个隐式状态，**无需码本**，因此不可能出现码本坍缩。涨跌停、跳空和放量 K 线落在边缘档位，而不会被抹平。联合状态利用率 71.4 %，归一化熵 0.87。
- **四个小分类器代替一个大词表。** 解码器每步只预测 8 + 8 + 8 + 5 = **29** 个类别，而不是 2,560 个，一次骨干前向即可完成。
- **确定性分位数推理。** 单调分位数头一次前向直接输出 q0.1 / q0.5 / q0.9，无需蒙特卡洛采样，全股票池推理比 5 路采样快约 80 %。80 % 预测区间覆盖了 78.6 % 的实际结果。
- **用预测不确定性驱动风控。** 预测区间宽度经保序回归映射为未来波动率（样本外冻结），用于波动率预算、回撤阶梯和股指期货对冲阶梯。
- **可审计的评估。** 严格时间隔离、所有基线同一协议、成本前置扣除，并从 2017 年起做逐年完整重训的 walk-forward。
- **按系统工程构建。** 6 个 Docker 服务、单写者 parquet 数据湖与时点一致读取、带内存准入控制的因子队列，以及端到端链路追踪。

## 系统架构

<img src="assets/readme/architecture.svg" alt="系统架构" width="100%">

**FSQ 引擎**（上半部分）是研究核心，在单卡 GPU 上离线训练并输出因子面板。
**平台**（下半部分）为引擎提供受治理的行情数据，并通过 [`FSQAlpha` / `FSQWidth`](packages/quant_factor/src/quant_factor/builtin/fsq.py) 内置因子加载面板。FSQ 因此与经典因子使用同一套机器、同一条审计链完成评估、中性化和相关性分析。

<details>
<summary><b>服务拓扑</b>（6 个容器，1 个公网端口）</summary>

| 服务 | 职责 | 存储 |
| :-- | :-- | :-- |
| `web-frontend` | nginx：React 单页应用 + 反向代理，唯一公网入口 | — |
| `web-backend` | 网关、认证（JWT + RBAC）、审计、日志中枢、只读控制台、监控评估器 | `/data:ro`、`./state` |
| `data-keeper` | **唯一写者**：入库 API、进程内任务执行器与调度器、对账/回收 | `/data:rw` |
| `factor-api` | 因子库、运行、因子篮子、因子优选 | `/data:ro`、`./factor-store` |
| `factor-worker` | 评估队列：IC/IR、分层、中性化、相关性 | `/data:ro`、`./factor-store` |
| `obs-sentinel` | 进程外看门狗：存活、OOM、重启风暴、磁盘、CPU、core dump | `docker.sock:ro` |

读取直接走库、读只读 parquet；写入只经过一个服务。每个请求都带一个 `trace_id`，跨服务汇入同一个日志中枢。

</details>

## FSQ 模型详解

<img src="assets/readme/fsq-model.svg" alt="FSQ tokenizer 与自回归模型" width="100%">

### 为什么用标量量化

| | 可学习码本 tokenizer（VQ / BSQ，分层） | **FSQ 并行分组（本项目）** |
| :-- | :-- | :-- |
| 离散状态 | 可学习码本 + 最近邻查找 | 隐式笛卡尔网格：8 × 8 × 8 × 5 = 2,560 |
| 辅助损失 | 承诺项与熵项，存在码本坍缩风险 | 无，从结构上杜绝坍缩 |
| 极端 K 线 | 离群值被拉向最近的码字 | 有界 `tanh` 将其映射到边缘档位，保留形态 |
| token 结构 | 由粗到细，顺序解码 | 4 个对称子 token 由同一隐状态**并行**预测 |
| 预测头 | 全词表上一个 softmax | 4 个头，合计 **29 类**；深度不随容量增长 |
| 推理 | 多条蒙特卡洛路径取平均 | 一次确定性前向，直接输出分位数 |

**状态空间的选择**（早期基准数据集实验）：

| 配置 | 档位 | 状态数 | 重构 NRMSE ↓ | 因子 RankIC ↑ | 推理耗时 |
| :-- | :-- | --: | --: | --: | --: |
| FSQ-S | `[8, 8, 8]` | 512 | 0.086 | 0.041 | 1.00× |
| **FSQ-B（采用）** | **`[8, 8, 8, 5]`** | **2,560** | **0.078** | **0.047** | **1.07×** |
| FSQ-L | `[8, 8, 6, 5, 5]` | 9,600 | 0.074 | 0.048 | 1.15× |

第四个标量带来了大部分收益；第五个提升有限，额外开销却翻倍。

**训练语料上的 token 使用情况：** 每个维度的档位 100 % 被使用，平均归一化熵 0.87，2,560 个联合状态中 71.4 % 处于活跃，前 10 % 的状态覆盖 46.2 % 的 K 线，没有退化维度。

### 模型族

| 模型 | 层数 | d_model | FFN | 头数 | 参数量 | 学习率 | 权重衰减 | Dropout（ffn / resid / attn / token） |
| :-- | --: | --: | --: | --: | --: | --: | --: | :-- |
| Tokenizer | 2 + 2 | 192 | 768 | 6 | 1.78 M | 3e-4 | 0.01 | — |
| **FSQ-compact** | 8 | 448 | 1,344 | 7 | 16.18 M | 8e-4 | 0.02 | 0.15 / 0.15 / 0.10 / 0.05 |
| **FSQ-base** | 10 | 640 | 1,920 | 10 | 41.15 M | 5e-4 | 0.05 | 0.10 / 0.10 / 0.05 / 0.00 |

<sub>参数量以 <code>kbarfm.model.build_model</code> 实际构建为准。上下文长度 384 步，预测期 H = 10 日，推理回看 90 日。训练使用 AdamW，1,500 步预热后接余弦调度。
目标函数为 Σ 分组交叉熵 + 0.5 × pinball 损失，自采样概率在 6 万步内升至 p = 0.15。</sub>

### 训练

<img src="assets/readme/chart-corpus.svg" alt="语料治理漏斗" width="100%">

| 阶段 | 步数 | 批量 × 上下文 | 数据量 | 耗时 | 显存峰值 |
| :-- | --: | :-- | :-- | --: | --: |
| Tokenizer | 200,000 | 256 × 128 | 约 65.5 亿根 K 线 | 约 8.7 h | 9.8 GB |
| 预训练 · compact | 120,000 | 16 × 384（累积 4） | 约 7.4 亿 token | 约 4.6 h | 12.6 GB |
| 预训练 · base | 120,000 | 16 × 384（累积 4） | 约 7.4 亿 token | 约 8.9 h | 22.3 GB |
| 样本外因子生成 | — | 256（推理） | 约 18.7 万股·日 | 约 1.6 h | 6.4 GB |
| **合计** | | | | **约 24 GPU·h** | |

<sub>全部在单张 RTX 4090 / 4090D（24 GB）上完成，环境为 CUDA 12.6 容器 + PyTorch 2.7.0。BF16、梯度检查点、SDPA 注意力和梯度累积使 base 模型能在消费级显卡上训练。
全局随机种子为 42，重复运行时关键指标波动小于 ±0.2 %。base 模型验证损失从 7.85 降至约 4.51（compact 约 4.74），训练/验证差距很小。</sub>

## 实验结果

**实验协议。**

- 预训练语料截止于 2025-06-30，测试区间为 2025-07-01 至 2026-06-18，任何参数都不在测试区间上调优。
- 所有模型与基线使用相同的股票池、规则和成本：
  - 沪深 300 与中证 800 成分股，剔除停牌、ST 和涨跌停无法成交的股票；
  - 只做多，等权 top-k（k = 50 / 200），配合 drop-n（n = 5 / 10）；
  - 每 5 日调仓，最短持有 5 日；
  - 单边成本 0.15 %；
  - 在 Qlib 中模拟。
- 因子使用后复权价格计算，成交使用真实价格。

| 指标 | 沪深 300 股票池 | 中证 800 股票池 |
| :-- | --: | --: |
| 累计收益（基准） | **+38.3 %**（+26.0 %） | **+44.9 %**（+28.7 %） |
| 年化超额收益（AER） | **13.5 %** | **17.8 %** |
| 信息比率 | 1.28 | 1.42 |
| 夏普比率（rf = 2 %） | 2.49 | 2.64 |
| 最大回撤 | −7.3 % | −8.3 % |
| Calmar 比率 | 5.70 | 5.89 |
| 月度胜率（超额） | 66.7 % | 75.0 % |
| 因子 RankIC 均值 · ICIR · Newey-West t | 0.058 · 0.52 · 8.0 | 0.063 · 0.57 · 8.7 |
| RankIC 为正的交易日占比 | 68.4 % | 71.1 % |

### 与其他预测模型对比

<img src="assets/readme/chart-baselines.svg" alt="与时间序列基础模型对比" width="100%">

Moment、Moirai 和 TimesFM 使用官方预训练权重；PatchTST 和 TimeXer 在相同训练窗口上训练。唯一变化的是信号来源。
base 模型在两个股票池上都优于 compact 模型，与规模效应一致。

### 不同市场状态

<img src="assets/readme/chart-regimes.svg" alt="分市场状态表现" width="100%">

测试年度的四种市场状态下超额收益均为正，在个股分化时（震荡和下跌市）最大，这正是量价信息最密集的阶段。2—3 月回撤期间，策略下跌 2.3 %，指数下跌 5.2 %。

### 九年逐年重训（walk-forward）

<img src="assets/readme/chart-walkforward.svg" alt="walk-forward 逐年超额收益" width="100%">

每一期模型的构建方式：

- 每年一期模型（2017—2025），每期只使用截至上一年末的数据训练。
- 每期重跑完整链路：语料、tokenizer、基础模型、分位数头和波动率校准器。
- 各期配置冻结，逐年零调参。

结果：

- 2017-01 至 2025-06 年化超额收益为 **11.2 %**（沪深 300）和 **14.4 %**（中证 800），IR 分别为 1.03 / 1.19。
- 每个测试年度超额收益均为正，经历了 2018 年熊市、2021 年风格切换和 2024 年小盘股流动性冲击。

### 超额收益来源

<img src="assets/readme/chart-attribution.svg" alt="超额收益归因" width="100%">

### 稳健性检验

- **因子质量。**
  - 十分组组合严格单调：最高组年化超额 +19.8 %，最低组 −13.2 %，多空价差约 33 个百分点。
  - RankIC 在 H = 10 日时最高（0.063），H = 20 日时仍为 0.041。
- **交易成本。**
  - 单边成本翻倍至 0.30 % 时，AER 仍有 12.6 % / 17.1 %。
  - 盈亏平衡单边成本约为 2.3 % / 4.0 %，远高于 A 股机构实际成本。
- **组合参数。** 在 k 和调仓频率的相邻取值上，AER 变化不超过约 2 个百分点。基准设定位于一个宽平台上，而非尖峰。
- **因子构造。**
  - 除以 q0.9 − q0.1 区间宽度，相比只用中位数，RankIC 相对提升约 8—12 %。
  - 更窄（q0.25 / q0.75）或更宽（q0.05 / q0.95）的区间效果都更差。

### 局限性

- 严格样本外窗口只有一年（235 个交易日），未覆盖完整的牛熊周期。
- walk-forward 属于**准样本外**：每期都遵守数据截止日，但架构和超参数是在看过完整历史后确定的。它是辅助证据，而非主要结论。
- 标注为*早期实验*的数据（状态空间权衡、基准数据集 IC）来自另一个历史基准数据集，不能与样本外数字直接比较。
- 模型只使用量价数据。基本面、行业结构和新闻是自然的下一步输入。

## 风险管理

<img src="assets/readme/risk.svg" alt="风险管理阶梯" width="100%">

风控作用于三个层面：个股、组合和信号。

- 预测波动率与因子来自同一次前向，因此风险预算在**建仓之前**就已确定，而不是等亏损出现后再调整。
- 样本外期间，沪深 300 组合 3 次进入一级观察区，从未触及二级。
- 中证 800 组合在 2025 年 12 月有 1 天触及二级，降仓后恢复。
- 两个股票池均从未触及三级。

## 研究平台

FSQ 因子与因子库中的其他因子位于同一个控制台中，旁边是行情数据、数据质量工具和实时系统健康状态。
界面采用信息密集、键盘驱动的浅色主题，支持中英文切换。

<table>
<tr>
<td colspan="2"><img src="assets/readme/screens/factor-library.png" alt="因子库"><br><sub><b>因子库。</b><code>fsqalpha</code> 和 <code>fsqwidth</code> 作为内置因子与经典因子并列注册。每个因子拥有相同的指标列：RankIC、ICIR、胜率、多空收益、换手、衰减和每日跟踪。</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/readme/screens/kline.png" alt="K 线工作台"><br><sub><b>K 线工作台。</b>多频率 K 线、复权模式、技术指标，以及逐根 K 线的数据血缘（批次、来源、入库时间）。</sub></td>
<td width="50%"><img src="assets/readme/screens/dashboard.png" alt="数据总览"><br><sub><b>数据治理。</b>数据表健康度、批次台账、数据源优先级和治理热点。</sub></td>
</tr>
<tr>
<td width="50%"><img src="assets/readme/screens/observability.png" alt="可观测"><br><sub><b>可观测。</b>分层健康、主机资源、崩溃转储监视和告警生命周期。</sub></td>
<td width="50%"><img src="assets/readme/screens/topology.png" alt="拓扑"><br><sub><b>拓扑。</b>来自进程外哨兵的实时容器拓扑图。</sub></td>
</tr>
</table>

## 安装指南

根据你的目的，有三条路径：

| 我想要… | 路径 | 需要 GPU？ | 耗时 |
| :-- | :-- | :-- | :-- |
| 用演示数据体验平台 | [A · 运行平台](#a--用演示数据运行平台) | 不需要 | 约 15 分钟（首次构建） |
| 训练 FSQ 并生成自己的因子面板 | [B · 训练 FSQ 模型](#b--训练-fsq-模型) | 需要，NVIDIA 24 GB | 全流程约 24 GPU·h |
| 阅读或修改代码、运行测试 | [C · 本地开发](#c--本地开发) | 不需要 | 约 5 分钟 |

### 环境要求

| | Linux | macOS | Windows |
| :-- | :-- | :-- | :-- |
| **支持范围** | x86-64 或 arm64，任意较新的发行版 | 12 及以上，Intel 或 Apple 芯片 | 10（22H2）/ 11，通过 **WSL 2** |
| **容器运行时** | [Docker Engine](https://docs.docker.com/engine/install/) 24+ 及 Compose v2 插件 | [Docker Desktop](https://docs.docker.com/desktop/setup/install/mac-install/) 4.25+ | [Docker Desktop](https://docs.docker.com/desktop/setup/install/windows-install/) 4.25+，使用 WSL 2 后端 |
| **Git** | 发行版自带的包 | `xcode-select --install` | [Git for Windows](https://git-scm.com/download/win)，或 WSL 内的 Git |
| **路径 B（训练）** | NVIDIA 驱动 550+ 及 [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) | 不支持（无 CUDA） | Windows 上安装 NVIDIA 驱动 550+，配合 [CUDA on WSL](https://docs.nvidia.com/cuda/wsl-user-guide/) |
| **路径 C（开发）** | Python 3.11+、Node.js 20+ | Python 3.11+、Node.js 20+ | 同左，装在 WSL 内 |

请给 Docker 分配至少 **4 个 CPU、8 GB 内存和 15 GB 可用磁盘**。Docker Desktop 在 *Settings → Resources* 中设置。
开始前先确认环境：

```bash
docker version            # Client 和 Server 都要有输出
docker compose version    # v2.20 或更新
```

> [!TIP]
> **Windows 用户请在 WSL 2 中操作。** 打开 Ubuntu 终端，把仓库克隆到 Linux 文件系统里（例如 `~/src`），不要放在 `/mnt/c/` 下。
> 这样构建速度快好几倍，下面所有命令也与 Linux 完全相同。
> 如果更习惯 PowerShell，除了标注 *PowerShell* 的几行，其余命令都一样。

### A · 用演示数据运行平台

**1. 克隆仓库。**

```bash
git clone https://github.com/sjbsjb233/ZJ_Quant_03.git
cd ZJ_Quant_03/deploy
```

**2. 创建配置文件。** 所有配置都在 `deploy/.env` 这一个文件里，它不会被提交到 Git。

```bash
cp .env.example .env
```

用任意编辑器打开 `.env`，修改下面几行，其余保持默认即可。

```ini
WEB_PORT=8080                  # 平台唯一对外端口；桌面系统上 80 端口常被占用
ALLOWED_ORIGINS=http://localhost:8080
ENVIRONMENT=DEV
JWT_SECRET=<一段较长的随机字符串>
LOG_INGEST_TOKEN=<另一段较长的随机字符串>
ENABLE_SCHEDULER=false         # 演示不需要行情数据 token，关闭调度器
```

两个密钥可以用 `openssl rand -hex 32`（Linux、macOS、WSL）或 `[guid]::NewGuid().ToString("N")`（*PowerShell*）生成。

**3. 让 Compose 使用本地构建叠加层。** 这样之后每条命令都不用再写 `-f docker-compose.yml -f docker-compose.dev.yml`。

```bash
export COMPOSE_FILE=docker-compose.yml:docker-compose.dev.yml      # Linux、macOS、WSL
```

```powershell
$env:COMPOSE_FILE = "docker-compose.yml;docker-compose.dev.yml"   # PowerShell（注意是分号）
```

**4. 构建镜像。** 首次构建需要下载基础镜像以及 Python 和 Node 依赖，约 5—15 分钟；之后会使用缓存。

```bash
docker compose build
```

**5. 导入演示数据集和演示用户。** 两条命令都只需执行一次，重复执行也无妨。

```bash
docker compose run --rm data-keeper python -m keeper_service.seed_demo
docker compose run --rm web-backend python -m quant_web.seed_users
```

**6. 启动服务。**

```bash
docker compose up -d
docker compose ps        # 等到所有服务显示 "running" 或 "healthy"（约 30 秒）
```

**7. 打开控制台** <http://localhost:8080>，用 `admin` / `admin123` 登录。
界面默认是英文，点击顶栏的 `中文` 按钮即可切换为中文。

<details>
<summary><b>停止、更新、重置</b></summary>

```bash
docker compose stop                   # 停止，保留所有内容
docker compose down                   # 删除容器，保留数据（不要加 -v）
git pull && docker compose up -d --build   # 更新到最新代码
```

所有状态都保存在 `deploy/` 下的 5 个目录中：`data/`、`state/`、`keeper-store/`、`factor-store/` 和 `logs/`。
执行 `docker compose down` 后复制整个 `deploy/` 目录，即可备份或迁移一套部署。
如需从头开始，先执行 `docker compose down`，删除这 5 个目录，再重复第 5—6 步。在 Linux 上这些目录属于 root，需要用 `sudo rm -rf`。

</details>

<details>
<summary><b>常见问题</b></summary>

| 现象 | 解决方法 |
| :-- | :-- |
| `bind: address already in use` | 端口被其他程序占用。修改 `.env` 中的 `WEB_PORT`（以及 `ALLOWED_ORIGINS`）。dev 叠加层还会为调试暴露 8000、8100 和 8300 端口，请释放这些端口，或删除 `docker-compose.dev.yml` 中对应的 `ports:` 配置。 |
| Linux 上出现 `permission denied … docker.sock` | 用 `sudo usermod -aG docker $USER` 把自己加入 docker 组，然后注销并重新登录。 |
| Windows 上容器启动时报 `exec … no such file or directory` | 脚本被检出成了 Windows 换行符。请在 WSL 内克隆，或执行 `git config --global core.autocrlf input` 后重新克隆。 |
| 某个服务反复重启，或构建被中途杀掉 | Docker 内存不足。在 Docker Desktop 中调到 8 GB 或以上。 |
| 在中国大陆首次构建很慢 | 为 Docker 配置镜像加速，并为 pip 和 npm 配置国内镜像源。 |

每个服务都输出结构化日志。`docker compose logs -f web-backend` 可以跟踪单个服务；控制台的 **可观测 → 日志** 页面按 `trace_id` 把所有服务的日志串在一起。

</details>

### B · 训练 FSQ 模型

训练需要 CUDA GPU。参考环境是单张 RTX 4090（24 GB），运行在 Linux 或 Windows + WSL 2 上。训练流水线在独立容器中运行，与平台服务互不影响。

**1. 确认 Docker 能识别 GPU。**

```bash
docker run --rm --gpus all nvidia/cuda:12.6.0-base-ubuntu22.04 nvidia-smi
```

**2. 准备行情数据。** 流水线不会自动下载行情，它读取你提供的 parquet 文件：

| 路径（位于 `packages/fsq_kbar/` 下） | 内容 | 使用阶段 |
| :-- | :-- | :-- |
| `data/raw/<freq>/<symbol>.parquet` | 每只股票的 OHLCV + 成交额 K 线，`freq` ∈ `1min 5min 15min 30min 60min day week` | 语料、tokenizer、预训练 |
| `data/panel/daily_panel.parquet` | 交易股票池的日频长表（后复权） | 校准、因子、评估 |

数据中应包含退市股。日期范围和语料截止日在 [`configs/`](packages/fsq_kbar/configs) 中设置。

**3. 运行流水线。**

```bash
cd packages/fsq_kbar/docker
docker compose build
docker compose run --rm train all          # 语料 → tokenizer → 预训练 → 校准 → 因子 → 评估
docker compose run --rm train pretrain     # …或逐个阶段运行
```

模型权重写入 `checkpoints/`，结果写入 `outputs/`，因子面板位于 `outputs/factor/factor_panel.parquet`。
可选步骤（逐年 walk-forward 重训、Qlib 回测、SuperMind 执行策略）见 [`packages/fsq_kbar/README.md`](packages/fsq_kbar/README.md)。

**4. 把因子加载到平台。** 将因子面板复制到平台的因子存储目录：

```bash
mkdir -p ../../../deploy/factor-store/fsq
cp ../outputs/factor/factor_panel.parquet ../../../deploy/factor-store/fsq/
```

也可以把 `factor-api` 和 `factor-worker` 的环境变量 `FSQ_FACTOR_PANEL` 指向该文件。
之后即可在 **因子管理 → 因子库 → 评估** 中，对任意股票池评估 `fsqalpha` 和 `fsqwidth`。

<details>
<summary><b>没有 GPU？在 CPU 上检查模型</b></summary>

模型代码可以在 CPU 上运行（macOS 也可以），足以查看模型结构、做小规模推理实验：

```bash
cd packages/fsq_kbar
python3 -m venv .venv && source .venv/bin/activate        # PowerShell：.venv\Scripts\Activate.ps1
pip install torch==2.7.0 --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
python -c "from kbarfm.model import build_model; print(sum(p.numel() for p in build_model('base').parameters()))"
```

</details>

### C · 本地开发

Python 服务和库按依赖顺序，以可编辑模式装进同一个虚拟环境：

```bash
python3 -m venv .venv && source .venv/bin/activate        # PowerShell：.venv\Scripts\Activate.ps1
bash scripts/install_dev.sh                               # Windows 上请在 WSL 或 Git Bash 中运行

# 每个包有自己的 pytest 配置，需要进入包目录运行测试
(cd packages/quant_factor && pytest)                      # 因子库，含 FSQ 内置因子
(cd packages/quant_data && pytest)
(cd apps/factor && pytest)
```

对正在运行的服务（路径 A）以热更新方式启动控制台：

```bash
cd apps/web/frontend
npm ci
npm run dev              # http://localhost:5173；/api 代理到 8000 端口的 web-backend
```

CI 会在每个 pull request 上运行同样的后端测试和前端生产构建。

## 仓库结构

```
ZJ_Quant_03/
├── packages/
│   ├── fsq_kbar/              ★ FSQ 引擎：kbarfm/（data · model · train · factor · risk · portfolio · eval）
│   │   ├── scripts/              语料 → tokenizer → 预训练 → 校准 → 因子 → 评估 → walk-forward
│   │   ├── configs/              全部超参数，每个阶段一个 YAML
│   │   ├── supermind/            执行端策略（股票 + 股指期货）
│   │   └── qlib_backtest/        Qlib 两遍式风控回测、归因、敏感性分析
│   ├── quant_factor/          因子契约、评估引擎、中性化、相关性
│   │   └── src/quant_factor/builtin/fsq.py   ← FSQAlpha / FSQWidth
│   ├── quant_data/            时点一致 parquet 数据湖、治理、质量、血缘
│   └── quant_strategy/        策略库（平台路线图）
├── apps/
│   ├── data-keeper/           单写者入库 + 调度
│   ├── factor/                因子 API + worker 队列
│   ├── web/                   React 控制台 + FastAPI 网关
│   └── obs-sentinel/          进程外看门狗
├── deploy/                    docker-compose（生产 + dev 叠加层）、.env 模板
└── assets/readme/             本 README 的图（图表由 src/make_charts.py 生成）
```

## 参与贡献

欢迎提交 issue 和 pull request。开发在功能分支上进行，通过 pull request 合并到 `develop`；`main` 只接收来自 `develop` 的发布。
请保持每个 pull request 聚焦，为行为变化补充测试，并确保 CI 通过。

## 路线图

| 方向 | 范围 | 状态 |
| :-- | :-- | :-- |
| FSQ 引擎 | tokenizer、基础模型、因子、风控、Qlib/SuperMind 回测、walk-forward | ✅ 已完成 |
| 数据层 | 受治理的时点一致数据湖、质量、血缘、入库适配器 | ✅ 已上线 |
| 因子层 | 因子库、评估、中性化、相关性、因子篮子、因子优选 | ✅ 已上线 · v0.1 |
| 策略模块 | 在平台界面内完成组合构建与回测 | 🛠️ 2026 年 Q4 |
| 实盘交易 | 交易执行与持仓/风险监控 | 🗓️ 2027 年 |
| FSQ 后续 | 更大的语料和模型、引入基本面与行业数据、定期增量重训 | 🔬 研究中 |

## 致谢

FSQ 借鉴了以下工作的思想：FSQ（Mentzer et al., 2023）、Qlib（Yang et al., 2020）、RoFormer（Su et al., 2024），以及时间序列基础模型方向的研究（Chronos、TimesFM、Moirai、MOMENT、Time-MoE、Timer、Sundial）。

<sub>本仓库为研究代码，不构成任何投资建议。</sub>

<br>

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/zj-logo-dark.svg">
  <img src="assets/zj-logo.svg" alt="ZJ Quant" width="96">
</picture>
<br>
<sub><em>From clean data to live alpha.</em></sub>
</div>
