// English strings for the shell (module strip, sidebars, status bar) and shared
// primitives/formatters, keyed by the original Chinese text.
export default {
  // modules
  '数据管理': 'Data',
  '数据可视化': 'Charts',
  '因子管理': 'Factors',
  '可观测': 'Observability',
  '策略管理': 'Strategies',
  '实盘交易': 'Live trading',
  '{label} · 计划 {phase}': '{label} · planned {phase}',

  // factor nav
  '因子库': 'Factor library',
  '因子篮子': 'Baskets',
  '因子优选': 'Screener',
  '入选集合': 'Selections',
  '任务队列': 'Task queue',
  '股票池': 'Universes',
  '因子导入': 'Import',

  // data nav
  '总览': 'Overview',
  '批次台账': 'Batches',
  '数据源': 'Sources',
  '字段注册表': 'Field registry',
  '表浏览': 'Tables',
  '数据质量': 'Data quality',
  '维护操作': 'Maintenance',
  '审计日志': 'Audit log',

  // observability nav
  '告警': 'Alerts',
  '拓扑': 'Topology',
  '日志': 'Logs',
  '实时流': 'Live stream',
  '会话': 'Sessions',
  '任务': 'Tasks',
  '规则': 'Rules',

  // viz nav
  '可视化中心': 'Chart hub',
  '市场全景': 'Market',
  '行业视图': 'Industries',
  'K 线工作台': 'K-line workbench',
  '财务时间序列': 'Fundamentals',
  '通用图表浏览器': 'Chart explorer',

  // top bar / sidebar footer
  '切换主题': 'Toggle theme',
  '切换语言': 'Switch language',
  '登出': 'Sign out',
  '状态徽标速查': 'Status badge guide',
  '命令面板': 'Command palette',

  // status bar
  '正常': 'healthy',
  '轮询': 'polling',
  '降级': 'degraded',
  '观测中断': 'blind',
  '故障': 'down',
  '实时观测中断 · 哨兵与后端均不可达 · 点击进入可观测': 'Live monitoring lost · sentinel and backend unreachable · open Observability',
  '系统状态 (SSE 断, 轮询回退) · 点击进入可观测': 'System status (SSE lost, polling fallback) · open Observability',
  '系统状态 (哨兵实时裁决) · 点击进入可观测': 'System status (live sentinel verdict) · open Observability',
  '点击进入日志中心': 'open in logs',
  '不可达': 'unreachable',
  '可达': 'reachable',
  '后端': 'Backend',
  '前端': 'Frontend',
  '探测': 'probe',
  '重启': 'restarts',
  '上次成功': 'last success',
  '数据层': 'Data layer',
  '因子层': 'Factor layer',
  '监控层': 'Monitor layer',
  '哨兵': 'sentinel',
  '在线': 'online',
  '离线/未上线': 'offline',
  '实时': 'live',
  '哨兵 SSE': 'sentinel SSE',
  '轮询回退': 'polling fallback',
  '中断': 'lost',
  'web-backend 元数据库 (审计 + app_log)': 'web-backend metadata DB (audit + app_log)',
  'parquet 数据湖 /data (所有数据读取)': 'Parquet data lake /data (all reads)',
  '{n} 条触发中告警 · 点击查看': '{n} firing alert(s) · click to view',

  // primitives
  '加载中…': 'Loading…',
  '暂无数据': 'No data',
  '重试': 'Retry',
  '确认执行': 'Confirm',
  '取消': 'Cancel',
  '执行中…': 'Running…',
  '我已确认这是经过审慎判断的操作': 'I have reviewed this operation and want to proceed',
  '请输入': 'Type',
  '以确认:': 'to confirm:',

  // relative time
  '刚刚': 'just now',
  '{n} 秒前': '{n}s ago',
  '{n} 分钟前': '{n}m ago',
  '{n} 小时前': '{n}h ago',
  '{n} 天前': '{n}d ago',
  '昨': 'Yday',
  '今天': 'today',
  '昨天': 'yesterday',
};
