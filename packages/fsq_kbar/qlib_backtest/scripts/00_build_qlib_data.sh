#!/usr/bin/env bash
# =====================================================================
# 第0步：准备Qlib本地行情数据库（第3章数据底座）
# 全流程只用Qlib：日频与1分钟频入库后，其余频率在语料构建时重采样。
# =====================================================================
set -e

QLIB_DIR=~/.qlib/qlib_data

# 方式一：官方社区数据（快速起步）
python3 -m qlib.run.get_data qlib_data --target_dir "$QLIB_DIR/cn_data" --region cn --interval 1d
python3 -m qlib.run.get_data qlib_data --target_dir "$QLIB_DIR/cn_data_1min" --region cn --interval 1min

# 方式二：自有CSV入库（全量A股含已退市标的，规避幸存者偏差，表3.2）
# CSV需含列: date,open,high,low,close,volume,amount（价格为后复权口径，7.1.3）
# python3 -m qlib.run.dump_bin dump_all \
#     --csv_path data/raw_csv_day --qlib_dir "$QLIB_DIR/cn_data" \
#     --freq day --include_fields open,high,low,close,volume,amount
# python3 -m qlib.run.dump_bin dump_all \
#     --csv_path data/raw_csv_1min --qlib_dir "$QLIB_DIR/cn_data_1min" \
#     --freq 1min --include_fields open,high,low,close,volume,amount

# 构建中证800成分文件（csi800 = csi300 ∪ csi500，3.1.1）
python3 - <<'EOF'
from kbarfm.data.qlib_source import ensure_csi800
print("csi800 ->", ensure_csi800("~/.qlib/qlib_data/cn_data"))
EOF

echo "Qlib数据库准备完成"
