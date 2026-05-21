# quant-data · 数据层

ZJ Quant 多因子量化交易系统的**数据层**（设计文档 §1.1）。它是一个独立的 Python
distribution（`name = "quant-data"`），可被 web 服务、CLI、Notebook 等任何调用方直接
`import quant_data` 使用。

## 语义模型

- **三类数据域**：`raw.*`（原始落库）、`reference.*`（公司行动等参考数据）、`metadata.*`
  （批次台账、来源注册表、字段注册表、质量问题、表统计）。
- **Append-only + current-view**：每次写入产生新批次，历史不可变；读取默认呈现"当前有效"
  视图，可时间旅行到任意快照。
- **PIT（point-in-time）**：基本面等数据按"当时可知"口径读取，杜绝未来函数。
- **行级血缘（row lineage）**：每行可追溯到产生它的批次与被哪次修订取代。
- **字段可扩展**：未知字段先进 `_extras`，可经 `extras → promoted → core` 状态机晋升为正式列。

## 主要 API

```python
from quant_data import DataReader        # 当前视图 / 全量视图 / 单批次 / 行血缘 / _extras 发现
from quant_data import ingest            # 写入（产生批次）
from quant_data import migration         # 字段晋升（含 dry-run）
from quant_data import charts            # K 线标注（公司行动、批次修订带）
from quant_data.metadata import batches, sources, field_registry
```

## 安装与测试

本包是 monorepo 的一员，推荐从仓库根用 `bash scripts/setup_env.sh` 一次装齐。单独开发本包：

```bash
pip install -e ".[test]"     # 在 packages/quant_data/ 下
pytest                       # 169 通过（默认排除 perf；跑性能基线用 -m perf）
```

`src/` layout：源码在 `src/quant_data/`，测试在 `tests/`。必须先 `pip install -e .` 才能 `import`。
