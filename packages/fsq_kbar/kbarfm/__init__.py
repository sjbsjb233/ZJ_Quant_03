"""kbarfm — FSQ并行标记化与分位数风险调整的量价预测因子策略系统.

对应报告《FSQ并行标记化与分位数风险调整：基于金融基座大模型的
量价预测因子策略系统》的完整工程实现：

- kbarfm.data       训练数据准备与处理流程（报告第3章）
- kbarfm.model      K线标记化器与基座模型（报告第4章）
- kbarfm.train      标记化器训练与自回归预训练（报告4.1/4.2、附录A）
- kbarfm.factor     量价预测因子与前瞻波动率（报告4.2.4、5.1）
- kbarfm.risk       多维风险监控 / 回撤警戒 / 分级对冲（报告第5章）
- kbarfm.portfolio  建仓、调仓与现金管理（报告第6章）
- kbarfm.eval       因子检验与绩效评估（报告第7章）

执行端（SuperMind回测引擎）策略见仓库根目录 supermind/。
"""

__version__ = "1.0.0"
