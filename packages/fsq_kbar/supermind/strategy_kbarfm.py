# -*- coding: utf-8 -*-
"""SuperMind执行端策略：FSQ并行标记化与分位数风险调整的量价预测因子多头策略.

对应报告第5/6章的完整执行端实现，运行于同花顺SuperMind回测引擎
（init / before_trading / handle_bar / after_trading 框架，股票+股指期货
混合账户）。

信号来源三级降级（保证策略在任何环境下可运行）：
  一级：预计算信号文件 kbarfm_signal_data.py（由训练端
        scripts/04_generate_factor.py 生成，放置于SuperMind"我的研究"
        根目录）——推荐方式，模型在本地GPU离线推理；
  二级：在线推理（平台环境装有 torch 与 kbarfm 包时，盘后以90日
        回看窗口对全池做确定性单次前向）；
  三级：风险调整动量代理因子（20日动量/已实现波动率），仅作
        信号文件缺失时的降级兜底，保证回测链路完整。

风控与仓位（与报告表5.2/5.3/5.4/6.2一致）：
  - 波动率风险预算：预测波动率截面前20%分位降权、前3%剔除；
  - 量价拥挤三重信号（换手异常/量价背离/资金集中），两项共振处置；
  - 回撤三级警戒 5%/8%/12%：暂停开仓 -> 降仓至60% -> 降仓至25%；
  - 因子失效监控：20日滚动RankIC<0.02连续5日进入防御模式；
  - 对冲阶梯 0/20/60/80/100%：IF（沪深300池）/ IC（中证800池）空头；
  - 现金三档 3%/10%/危机倒推；drop-n约束与5日最短持有期。

使用：将本文件粘贴为SuperMind股票（股票期货混合）策略，回测频率
"每日"；如需盘中快速通道（报告5.3），改用"分钟"频率，策略自动
在9:31执行主节拍、其余整点分钟仅做监控。
"""

import datetime
import math

import numpy as np
import pandas as pd

# ============================================================ 策略参数 ====
# 标的池切换： 'hs300' -> 沪深300池(k=50, n=5, IF对冲)
#             'csi800' -> 中证800池(k=200, n=10, IC对冲)
POOL_NAME = 'hs300'

CFG = {
    'hs300': {
        'index': '000300.SH', 'top_k': 50, 'drop_n': 5, 'hedge_symbol': 'IF',
        'hedge_multiplier': 300,
    },
    'csi800': {
        'index': '000906.SH', 'top_k': 200, 'drop_n': 10, 'hedge_symbol': 'IC',
        'hedge_multiplier': 200,
    },
    # ---- 共用参数（报告表7.2 / 表5.2-5.4 / 表6.2）----
    'common': {
        'rebalance_every': 5,        # 每5个交易日调仓
        'min_holding_days': 5,       # 最短持有期
        'horizon': 10,               # 预测期H（RankIC监控回看用）
        'lookback': 90,              # 因子回看窗口（在线推理用）
        # 波动率风险预算（5.1节）
        'vol_downweight_pct': 0.80,  # 截面前20%分位降权
        'vol_exclude_pct': 0.97,     # 极端突出者（前3%）剔除
        'vol_downweight_mult': 0.5,
        # 回撤三级警戒（表5.3）
        'dd_l1': 0.05, 'dd_l2': 0.08, 'dd_l3': 0.12,
        'dd_release': 0.08, 'release_days': 3,
        'equity_scale_l2': 0.60,     # 二级：降仓至50%-70%取中值
        'equity_scale_l3': 0.25,     # 三级：降仓至20%-30%取中值
        'refill_step_days': 5,       # 解除后按周度逐级回补
        # 因子失效监控（表5.2）
        'ic_window': 20, 'ic_fail': 0.02, 'ic_recover': 0.03, 'ic_days': 5,
        # 拥挤监控（表5.2）
        'crowd_sigma': 2.0, 'crowd_to_q': 0.85,
        'crowd_pv_corr': -0.3, 'crowd_pv_pos': 0.90,
        'crowd_conc_q': 0.90,
        # 对冲阶梯（表5.4）
        'hedge_vol_pool_share': 0.30,   # 池内高波动成分占比>30% -> 二级条件
        'hedge_crowd_share': 0.20,      # 拥挤标的持仓占比>20% -> 二级条件
        # 现金三档（表6.2）
        'cash_normal': 0.03, 'cash_risk': 0.10,
        # 股票/期货账户资金划分
        'stock_cash': 10000000, 'future_cash': 3000000,
        # 模型文件路径（在线推理时使用；文件放于"我的研究"根目录）
        'tokenizer_ckpt': 'tokenizer_final.pt',
        'model_ckpt': 'base_final.pt',
    },
}


# ============================================================ 初始化 ====
def init(context):
    p = dict(CFG['common'])
    p.update(CFG[POOL_NAME])
    g.p = p

    # 股票 + 股指期货混合账户（期货用于分级动态对冲，表5.4）
    set_subportfolios([
        {'cash': p['stock_cash'], 'type': 'stock'},
        {'cash': p['future_cash'], 'type': 'future'},
    ])
    _try_platform_setup(p)

    # ---- 运行状态 ----
    g.day_idx = 0                    # 交易日计数
    g.days_since_rebalance = 999     # 保证首日即建仓
    g.holding_age = {}               # sec -> 持有交易日数
    g.pool = []                      # 当前标的池
    g.scores_today = {}              # sec -> 因子分（z值）
    g.vol_today = {}                 # sec -> 预测波动率
    g.crowded_today = set()          # 当日拥挤标的
    g.tradable_today = set()

    # 回撤三级警戒状态机（表5.3）
    g.dd = {'peak': None, 'dd': 0.0, 'level': 0, 'release': 0,
            'refill_days': 0, 'refill_from': 1.0}
    # 对冲阶梯状态机（表5.4）
    g.hedge = {'ratio': 0.0, 'release': 0, 'contract': None}
    # 因子有效性监控（表5.2）
    g.icmon = {'hist': [], 'defensive': False, 'below': 0, 'above': 0}
    # 现金三档（表6.2）
    g.cash = {'ratio': p['cash_normal'], 'calm': 0}
    # RankIC监控用信号快照：[{'day_idx':int, 'scores':{sec:score}}]
    g.snapshots = []

    # 信号提供方（三级降级）
    g.signal_provider = _load_signal_provider()
    log.info('[init] 标的池=%s k=%d n=%d 对冲=%s 信号来源=%s' % (
        POOL_NAME, p['top_k'], p['drop_n'], p['hedge_symbol'], g.signal_source))


def _try_platform_setup(p):
    """平台参数设置：不同版本API签名略有差异，逐一容错."""
    try:
        set_benchmark(p['index'])
    except Exception as e:
        _logw('set_benchmark失败: %s' % e)
    try:
        # 可变滑点0.2%（回测更贴近实盘冲击）
        set_slippage(PriceSlippage(0.002))
    except Exception as e:
        _logw('set_slippage失败: %s' % e)
    try:
        set_volume_limit(0.25, 0.25)
    except Exception as e:
        _logw('set_volume_limit失败: %s' % e)
    # 交易成本：报告按单边0.15%计提；平台默认佣金万2.5+印花税千1
    # 已接近该口径，如需精确对齐请按平台set_commission签名调整。
    try:
        set_commission(PerTrade(buy_cost=0.0003, sell_cost=0.0013, min_cost=5))
    except Exception:
        log.info('沿用平台默认佣金与印花税设置')


# ==================================================== 信号来源（三级） ====
def _load_signal_provider():
    """一级：预计算信号文件；二级：在线推理；三级：动量代理."""
    # 一级：kbarfm_signal_data.py（"我的研究"根目录）
    try:
        import kbarfm_signal_data as sig
        assert hasattr(sig, 'SIGNALS')
        g.signal_source = 'precomputed'
        return sig
    except Exception:
        pass
    # 二级：在线推理（需要 torch + kbarfm + 模型权重）
    try:
        import torch  # noqa: F401
        from kbarfm.model.io import load_tokenizer, load_foundation
        tok = load_tokenizer(CFG['common']['tokenizer_ckpt'])
        mdl = load_foundation(CFG['common']['model_ckpt'])
        g.signal_source = 'online'
        return {'tokenizer': tok, 'model': mdl}
    except Exception:
        pass
    g.signal_source = 'fallback'
    return None


def _get_signals(context, date_str):
    """返回 (scores: {sec: 因子z值}, vol_pred: {sec: 预测波动率}).

    信号统一为：alpha = q0.5/(q0.9-q0.1+1e-3) 经截面缩尾与z-score；
    vol_pred 为区间宽度经单调映射校准的未来10日已实现波动率估计。
    """
    if g.signal_source == 'precomputed':
        data = g.signal_provider.SIGNALS.get(date_str)
        if data:
            scores = dict((s, v[0]) for s, v in data.items())
            vols = dict((s, v[1]) for s, v in data.items())
            return scores, vols
        _logw('信号文件缺少日期 %s，降级为代理因子' % date_str)
    elif g.signal_source == 'online':
        try:
            return _online_inference(context)
        except Exception as e:
            _logw('在线推理失败(%s)，降级为代理因子' % e)
    return _fallback_signals(context)


def _online_inference(context):
    """二级：以90日回看窗口对全池做确定性单次前向（报告4.2.3/4.2.4）."""
    import torch
    p = g.p
    tok = g.signal_provider['tokenizer']
    mdl = g.signal_provider['model']

    fields = ['open', 'high', 'low', 'close', 'volume', 'amount']
    windows, stamps, secs = [], [], []
    for sec in g.pool:
        try:
            df = history(sec, fields, p['lookback'], '1d', True, 'post', is_panel=0)
        except Exception:
            continue
        if df is None or len(df) < p['lookback']:
            continue
        x = df[fields].values.astype('float32')
        if not np.isfinite(x[:, :4]).all():
            continue
        mean, std = x.mean(axis=0), x.std(axis=0)
        xn = np.clip((x - mean) / (std + 1e-5), -5.0, 5.0).astype('float32')
        idx = pd.DatetimeIndex(df.index)
        st = np.stack([idx.minute, idx.hour, idx.weekday, idx.day, idx.month],
                      axis=1).astype('int64')
        windows.append(xn)
        stamps.append(st)
        secs.append(sec)

    if not secs:
        return _fallback_signals(context)

    quants = np.zeros((len(secs), 3), dtype='float64')
    bs = 128
    with torch.no_grad():
        for lo in range(0, len(secs), bs):
            hi = min(lo + bs, len(secs))
            xb = torch.from_numpy(np.stack(windows[lo:hi]))
            sb = torch.from_numpy(np.stack(stamps[lo:hi]))
            codes = tok.encode(xb)
            q = mdl.predict_quantiles(codes, sb)
            quants[lo:hi] = q.float().numpy()

    q10, q50, q90 = quants[:, 0], quants[:, 1], quants[:, 2]
    width = np.clip(q90 - q10, 0.0, None)
    alpha = q50 / (width + 1e-3)                    # 风险调整信号（4.2.4）
    scores = _zscore_dict(secs, alpha)
    # 区间宽度直接作为前瞻波动率的序数代理（单调映射校准在训练端完成，
    # 此处仅用于横截面分位排序，序数信息与校准后一致——5.1节）
    vols = dict(zip(secs, width.tolist()))
    return scores, vols


def _fallback_signals(context):
    """三级降级：风险调整动量代理 alpha = 20日动量 / 20日已实现波动率."""
    secs, alphas, vols = [], [], []
    for sec in g.pool:
        try:
            df = history(sec, ['close'], 41, '1d', True, 'post', is_panel=0)
        except Exception:
            continue
        if df is None or len(df) < 41:
            continue
        close = df['close'].values.astype('float64')
        if not np.isfinite(close).all() or close[-21] <= 0:
            continue
        mom = close[-1] / close[-21] - 1.0
        rets = np.diff(np.log(close[-21:]))
        vol = float(np.std(rets, ddof=1)) if len(rets) > 2 else 0.0
        secs.append(sec)
        alphas.append(mom / (vol * math.sqrt(10.0) + 1e-3))
        vols.append(vol * math.sqrt(10.0))
    scores = _zscore_dict(secs, np.array(alphas)) if secs else {}
    return scores, dict(zip(secs, vols))


def _zscore_dict(secs, values):
    """截面1%/99%缩尾 + z-score（表7.1口径）."""
    v = np.asarray(values, dtype='float64')
    if len(v) < 5:
        return dict(zip(secs, v.tolist()))
    lo, hi = np.percentile(v, 1), np.percentile(v, 99)
    v = np.clip(v, lo, hi)
    mu, sd = v.mean(), v.std()
    z = (v - mu) / sd if sd > 0 else v * 0.0
    return dict(zip(secs, z.tolist()))


# ======================================================== 盘前准备 ====
def before_trading(context):
    g.day_idx += 1
    now = get_datetime()
    date_str = now.strftime('%Y-%m-%d')

    # 每20个交易日刷新一次指数成分（覆盖定期调样）
    if not g.pool or g.day_idx % 20 == 1:
        try:
            g.pool = list(get_index_stocks(g.p['index'], now.strftime('%Y%m%d')))
        except Exception as e:
            _logw('获取成分股失败: %s' % e)
    log.info('[%s] 第%d个交易日 | 池内%d只 | 距上次调仓%d日' % (
        date_str, g.day_idx, len(g.pool), g.days_since_rebalance))

    # 因子与波动率信号（日终主节拍生成的信号在次日盘前载入，5.3节）
    g.scores_today, g.vol_today = _get_signals(context, date_str)

    # 信号快照入库（供H日后RankIC回验）
    if g.scores_today:
        g.snapshots.append({'day_idx': g.day_idx, 'scores': dict(g.scores_today)})
        if len(g.snapshots) > 60:
            g.snapshots = g.snapshots[-60:]


# ======================================================== 主循环 ====
def handle_bar(context, bar_dict):
    now = get_datetime()
    # 分钟频率下：9:31执行主节拍，其余整30分钟仅做盘中快速通道监控
    if now.hour * 60 + now.minute > 9 * 60 + 35:
        if now.minute in (0, 30):
            _intraday_fast_channel(context, bar_dict)
        return

    p = g.p

    # ---- 1. 风险状态更新（度量 -> 监控 -> 响应，报告第5章）----
    nav = _total_value(context)
    vol_alert_share = _pool_vol_alert_share()
    vol_normal = vol_alert_share <= p['hedge_vol_pool_share']
    dd_level = _update_drawdown(nav, vol_normal)

    g.crowded_today = _detect_crowding(context)
    crowded_share = _crowded_holding_share(context)

    defensive = _update_ic_monitor(context)

    hedge_ratio = _update_hedge_ladder(dd_level, vol_alert_share, crowded_share)
    cash_ratio = _update_cash_tier(dd_level, vol_alert_share, crowded_share)
    equity_scale = _equity_scale(dd_level)

    # ---- 2. 持仓年龄推进 ----
    held = _current_holdings(context)
    for sec in held:
        g.holding_age[sec] = g.holding_age.get(sec, 0) + 1
    for sec in list(g.holding_age.keys()):
        if sec not in held:
            g.holding_age.pop(sec, None)

    # ---- 3. 分层处置（6.2.2）----
    # 三级警戒或降仓需求：立即按比例压缩持仓（不等调仓日）
    if dd_level >= 2:
        _derisk_holdings(context, bar_dict, equity_scale, cash_ratio)
    # 个股层面：持仓中触发拥挤/极端波动的标的立即处置
    _dispose_flagged_holdings(context, bar_dict)

    # ---- 4. 调仓主节拍（每5个交易日，6.2.1）----
    g.days_since_rebalance += 1
    is_rebalance_day = g.days_since_rebalance >= p['rebalance_every']
    if is_rebalance_day and not defensive and dd_level < 3:
        pause_new_buys = dd_level >= 1          # 一级及以上暂停新建仓位（表5.3）
        targets = _build_targets(context, bar_dict, equity_scale, cash_ratio,
                                 pause_new_buys)
        _execute_rebalance(context, bar_dict, targets)
        g.days_since_rebalance = 0
    elif is_rebalance_day:
        log.info('调仓日跳过：防御模式=%s 警戒级别=%d' % (defensive, dd_level))
        g.days_since_rebalance = 0

    # ---- 5. 对冲执行（5.4）----
    _manage_hedge(context, hedge_ratio)

    # ---- 6. 记录 ----
    record(drawdown=round(g.dd['dd'] * 100, 2),
           alert_level=g.dd['level'],
           hedge_ratio=round(hedge_ratio * 100, 1),
           n_holdings=len(_current_holdings(context)))


# ==================================================== 风险：回撤警戒 ====
def _update_drawdown(nav, vol_normal):
    """回撤三级警戒状态机（表5.3）：返回警戒级别0/1/2/3."""
    p, d = g.p, g.dd
    if d['peak'] is None or nav > d['peak']:
        d['peak'] = nav
    d['dd'] = 1.0 - nav / d['peak'] if d['peak'] > 0 else 0.0
    dd = d['dd']

    target = 0
    if dd >= p['dd_l3']:
        target = 3
    elif dd >= p['dd_l2']:
        target = 2
    elif dd >= p['dd_l1']:
        target = 1

    if target > d['level']:                     # 升级立即生效
        _logw('回撤警戒升级 L%d -> L%d（DD=%.2f%%）' % (
            d['level'], target, dd * 100))
        d['level'] = target
        d['release'] = 0
        d['refill_days'] = 0
    elif d['level'] >= 2:                       # 二级及以上按规则解除
        if dd < p['dd_release'] and vol_normal:
            d['release'] += 1
        else:
            d['release'] = 0
        if d['release'] >= p['release_days']:
            d['refill_from'] = _equity_scale(d['level'])
            d['level'] = 1 if dd >= p['dd_l1'] else 0
            d['release'] = 0
            d['refill_days'] = 1                # 进入周度回补期
            log.info('回撤警戒解除，进入周度逐级回补')
    elif d['level'] == 1 and dd < p['dd_l1']:
        d['level'] = 0

    if d['refill_days'] > 0:
        d['refill_days'] += 1
    return d['level']


def _equity_scale(level):
    """当前权益仓位系数（含解除后的周度逐级回补）."""
    p, d = g.p, g.dd
    if d['refill_days'] > 0 and level <= 1:
        start = d['refill_from']
        weeks = (d['refill_days'] - 1) // p['refill_step_days']
        scale = start + (1.0 - start) * min(weeks / 2.0, 1.0)   # 两周回补到位
        if scale >= 1.0:
            d['refill_days'] = 0
            return 1.0
        return scale
    if level >= 3:
        return p['equity_scale_l3']
    if level >= 2:
        return p['equity_scale_l2']
    return 1.0


# ==================================================== 风险：拥挤监控 ====
def _detect_crowding(context):
    """持仓标的的量价拥挤检测：三重信号任两项共振（表5.2）."""
    p = g.p
    crowded = set()
    held = _current_holdings(context)
    if not held:
        return crowded

    # 市场总成交额代理：基准指数成交额
    market_amt = None
    try:
        idx_df = history(p['index'], ['amount'], 61, '1d', False, 'pre', is_panel=0)
        if idx_df is not None and len(idx_df) >= 61:
            market_amt = idx_df['amount'].values.astype('float64')
    except Exception:
        pass

    for sec in held:
        try:
            df = history(sec, ['close', 'volume', 'amount'], 81, '1d',
                         True, 'pre', is_panel=0)
        except Exception:
            continue
        if df is None or len(df) < 61:
            continue
        close = df['close'].values.astype('float64')
        volume = df['volume'].values.astype('float64')
        amount = df['amount'].values.astype('float64') if 'amount' in df else None

        flags = 0
        # ① 换手异常：当日量相对20日均值偏离>2倍60日std，或60日85%分位以上
        today_v = volume[-1]
        mean20 = volume[-21:-1].mean()
        std60 = volume[-61:-1].std(ddof=1)
        q85 = float((volume[-60:] <= today_v).mean())
        if (std60 > 0 and abs(today_v - mean20) > p['crowd_sigma'] * std60) \
                or q85 >= p['crowd_to_q']:
            flags += 1
        # ② 量价背离：20日价量相关<-0.3 且价格处于20日区间上沿
        c20, v20 = close[-20:], volume[-20:]
        if v20.std() > 0 and c20.std() > 0:
            corr = float(np.corrcoef(c20, v20)[0, 1])
            rng = c20.max() - c20.min()
            pos = (c20[-1] - c20.min()) / rng if rng > 0 else 0.5
            if corr < p['crowd_pv_corr'] and pos >= p['crowd_pv_pos']:
                flags += 1
        # ③ 资金集中：成交额占比处于60日90%分位以上
        if amount is not None and market_amt is not None \
                and len(amount) >= 61 and len(market_amt) >= 61:
            share = amount[-60:] / np.maximum(market_amt[-60:], 1.0)
            q90 = float((share <= share[-1]).mean())
            if q90 >= p['crowd_conc_q']:
                flags += 1

        if flags >= 2:
            crowded.add(sec)
    if crowded:
        _logw('拥挤标的: %s' % sorted(crowded))
    return crowded


def _crowded_holding_share(context):
    """拥挤标的市值占持仓比例（对冲二级条件③）."""
    acct = context.portfolio.stock_account
    total = acct.total_value
    if total <= 0 or not g.crowded_today:
        return 0.0
    mv = 0.0
    for sec in g.crowded_today:
        pos = _position(acct, sec)
        if pos is not None:
            mv += pos.market_value
    return mv / total


def _pool_vol_alert_share():
    """池内处于横截面波动率前20%分位的成分占比（对冲二级条件②）.

    以当日预测波动率截面分位计，无信号时返回0。
    """
    if not g.vol_today:
        return 0.0
    v = pd.Series(g.vol_today).dropna()
    if len(v) < 10:
        return 0.0
    return float((v.rank(pct=True) >= g.p['vol_downweight_pct']).mean())


# ================================================ 风险：因子失效监控 ====
def _update_ic_monitor(context):
    """20日滚动RankIC监控（表5.2）：返回是否处于防御模式."""
    p, m = g.p, g.icmon

    # 用H个交易日前的信号快照与其后实现收益计算当日RankIC
    snap = None
    for s in g.snapshots:
        if g.day_idx - s['day_idx'] == p['horizon']:
            snap = s
            break
    if snap:
        ric = _realized_rank_ic(snap['scores'], p['horizon'])
        if ric is not None and np.isfinite(ric):
            m['hist'].append(float(ric))
            if len(m['hist']) > 250:
                m['hist'] = m['hist'][-250:]

    if len(m['hist']) >= p['ic_window']:
        rolling = float(np.mean(m['hist'][-p['ic_window']:]))
        record(rolling_rank_ic=round(rolling, 4))
        if not m['defensive']:
            m['below'] = m['below'] + 1 if rolling < p['ic_fail'] else 0
            if m['below'] >= p['ic_days']:
                m['defensive'] = True
                m['above'] = 0
                _logw('因子失效判定：20日RankIC=%.4f，进入防御模式' % rolling)
        else:
            m['above'] = m['above'] + 1 if rolling > p['ic_recover'] else 0
            if m['above'] >= p['ic_days']:
                m['defensive'] = False
                m['below'] = 0
                log.info('因子有效性恢复（20日RankIC=%.4f），退出防御模式' % rolling)
    return m['defensive']


def _realized_rank_ic(scores, horizon):
    """信号与其后horizon日实现收益的截面秩相关."""
    secs = list(scores.keys())
    if len(secs) < 20:
        return None
    rets = {}
    for sec in secs:
        try:
            df = history(sec, ['close'], horizon + 1, '1d', True, 'post', is_panel=0)
        except Exception:
            continue
        if df is None or len(df) < horizon + 1:
            continue
        c = df['close'].values.astype('float64')
        if c[0] > 0:
            rets[sec] = c[-1] / c[0] - 1.0
    common = [s for s in secs if s in rets]
    if len(common) < 20:
        return None
    a = pd.Series([scores[s] for s in common]).rank()
    b = pd.Series([rets[s] for s in common]).rank()
    return float(np.corrcoef(a.values, b.values)[0, 1])


# ==================================================== 风险：对冲阶梯 ====
def _update_hedge_ladder(dd_level, vol_alert_share, crowded_share):
    """对冲比例状态机（表5.4）：升级立即、降级需连续3日后逐级回落."""
    p, h = g.p, g.hedge
    ladder = (0.0, 0.20, 0.60, 0.80, 1.00)

    if dd_level >= 3:
        target = 1.00
    else:
        n_l2 = sum([dd_level >= 2,
                    vol_alert_share > p['hedge_vol_pool_share'],
                    crowded_share > p['hedge_crowd_share']])
        if n_l2 >= 2:
            target = 0.80
        elif n_l2 == 1:
            target = 0.60
        elif dd_level >= 1:
            target = 0.20
        else:
            target = 0.0

    if target >= h['ratio']:
        h['ratio'] = target
        h['release'] = 0
    else:
        h['release'] += 1
        if h['release'] >= p['release_days']:
            cur = min(ladder, key=lambda x: abs(x - h['ratio']))
            idx = max(0, ladder.index(cur) - 1)
            h['ratio'] = max(ladder[idx], target)
            h['release'] = 0
    return h['ratio']


def _manage_hedge(context, hedge_ratio):
    """IF/IC空头对冲执行：张数 = 组合市值×比例/(乘数×期价)，含主力换月."""
    p = g.p
    try:
        dominant = get_futures_dominate(p['hedge_symbol'])
    except Exception as e:
        _logw('获取主力合约失败: %s' % e)
        return
    if not dominant:
        return
    try:
        subscribe(dominant)
    except Exception:
        pass

    acct = context.portfolio.future_account
    old = g.hedge['contract']
    # 主力换月：先平旧合约空头
    if old and old != dominant:
        try:
            old_short = acct.positions[old].short_amount
        except Exception:
            old_short = 0
        if old_short > 0:
            order_future(old, old_short, 'close', 'short', None)
            log.info('主力换月：平%s空单%d手' % (old, old_short))
    g.hedge['contract'] = dominant

    # 目标张数
    stock_value = context.portfolio.stock_account.total_value
    try:
        fut_df = history_future(dominant, ['close'], 1, '1d', False, 'pre', is_panel=0)
        fut_price = float(fut_df['close'].values[-1])
    except Exception:
        fut_price = 0.0
    if fut_price <= 0:
        return
    target_n = int(round(stock_value * hedge_ratio /
                         (p['hedge_multiplier'] * fut_price)))

    try:
        cur_n = acct.positions[dominant].short_amount
    except Exception:
        cur_n = 0
    if target_n > cur_n:
        order_future(dominant, target_n - cur_n, 'open', 'short', None)
        log.info('对冲加仓：%s开空%d手（比例%.0f%%）' % (
            dominant, target_n - cur_n, hedge_ratio * 100))
    elif target_n < cur_n:
        order_future(dominant, cur_n - target_n, 'close', 'short', None)
        log.info('对冲减仓：%s平空%d手（比例%.0f%%）' % (
            dominant, cur_n - target_n, hedge_ratio * 100))


# ==================================================== 现金三档管理 ====
def _update_cash_tier(dd_level, vol_alert_share, crowded_share):
    """现金留存三档规则（表6.2）."""
    p, c = g.p, g.cash
    risk_on = (dd_level >= 2 or vol_alert_share > p['hedge_vol_pool_share']
               or crowded_share > p['hedge_crowd_share'])
    if dd_level >= 3:
        c['ratio'] = max(1.0 - p['equity_scale_l3'], p['cash_risk'])
        c['calm'] = 0
    elif risk_on:
        c['ratio'] = p['cash_risk']
        c['calm'] = 0
    else:
        c['calm'] += 1
        if c['calm'] >= p['release_days']:
            c['ratio'] = p['cash_normal']
    return c['ratio']


# ==================================================== 组合构建与执行 ====
def _build_targets(context, bar_dict, equity_scale, cash_ratio, pause_new_buys):
    """top-k / drop-n / 最短持有期 / 波动率预算 -> 目标权重（第6章）."""
    p = g.p
    scores = pd.Series(g.scores_today).dropna()
    if scores.empty:
        _logw('当日无有效因子信号，维持现有持仓')
        return None

    # 可交易性复核：剔除停牌、一字涨跌停、ST（6.1.1）
    tradable = set()
    for sec in scores.index:
        if _is_tradable(context, bar_dict, sec, for_buy=True):
            tradable.add(sec)
    g.tradable_today = tradable

    # 波动率风险预算（5.1）：前20%分位降权、前3%剔除
    vol = pd.Series(g.vol_today).reindex(scores.index).dropna()
    vol_excluded, vol_mults = set(), {}
    if len(vol) >= 20:
        pct = vol.rank(pct=True)
        vol_excluded = set(pct[pct >= p['vol_exclude_pct']].index)
        for sec in pct[(pct >= p['vol_downweight_pct'])
                       & (pct < p['vol_exclude_pct'])].index:
            vol_mults[sec] = p['vol_downweight_mult']

    candidates = scores[scores.index.isin(tradable)
                        & ~scores.index.isin(vol_excluded)
                        & ~scores.index.isin(g.crowded_today)]
    ranked = candidates.sort_values(ascending=False)
    ideal_top = list(ranked.index[:p['top_k']])
    rank_map = dict((s, i) for i, s in enumerate(ranked.index))

    held = _current_holdings(context)

    # ---- 卖出侧：强制处置优先，其余按排名劣化幅度取前n（drop-n）----
    sell_pool = []
    for sec in held:
        if sec in ideal_top:
            continue
        forced = (sec in vol_excluded) or (sec in g.crowded_today)
        age = g.holding_age.get(sec, 0)
        if age < p['min_holding_days'] and not forced:
            continue                                # 最短持有期（6.2.1）
        rank = rank_map.get(sec, len(ranked) + p['top_k'])
        sell_pool.append((rank, sec, forced))
    sell_pool.sort(key=lambda t: (not t[2], -t[0]))
    forced_sells = [s for _, s, f in sell_pool if f]
    optional_sells = [s for _, s, f in sell_pool if not f][:p['drop_n']]
    sells = forced_sells + optional_sells

    # ---- 买入侧：drop-n约束下按排名最优补入 ----
    keep = held - set(sells)
    buys = []
    if not pause_new_buys:
        slots = max(p['top_k'] - len(keep), 0)
        max_buys = min(p['drop_n'], slots)
        for sec in ideal_top:
            if len(buys) >= max_buys:
                break
            if sec not in keep:
                buys.append(sec)

    final = list(keep) + buys
    if not final:
        return {'weights': {}, 'sells': sells, 'buys': buys}

    # ---- 等权 + 波动率降权 + 归一到目标权益比例（6.1.2）----
    equity_ratio = (1.0 - cash_ratio) * equity_scale
    base = np.array([vol_mults.get(s, 1.0) for s in final], dtype='float64')
    base = base / base.sum() * equity_ratio
    weights = dict(zip(final, base.tolist()))

    log.info('目标组合：持仓%d只（买%d/卖%d）权益比例%.1f%% 波动率降权%d只' % (
        len(final), len(buys), len(sells), equity_ratio * 100, len(vol_mults)))
    return {'weights': weights, 'sells': sells, 'buys': buys}


def _execute_rebalance(context, bar_dict, targets):
    """先卖后买执行调仓；成交价为市价（开盘价+滑点）."""
    if not targets:
        return
    weights = targets['weights']
    acct = context.portfolio.stock_account
    total = acct.total_value
    held = _current_holdings(context)

    # 卖出：目标权重为0的持仓（含强制处置）
    for sec in sorted(held - set(weights.keys())):
        if _is_tradable(context, bar_dict, sec, for_buy=False):
            order_target_value(sec, 0)
            g.holding_age.pop(sec, None)
            log.info('卖出 %s' % sec)

    # 降低权重的持仓先行调整（释放资金）
    adjust = []
    for sec, w in weights.items():
        pos = _position(acct, sec)
        cur_v = pos.market_value if pos is not None else 0.0
        tgt_v = w * total
        adjust.append((tgt_v - cur_v, sec, tgt_v))
    adjust.sort()                                   # 先减仓后加仓

    for delta, sec, tgt_v in adjust:
        if abs(delta) < max(total * 1e-4, 1000.0):
            continue                                # 跳过碎单
        for_buy = delta > 0
        if not _is_tradable(context, bar_dict, sec, for_buy=for_buy):
            continue
        order_target_value(sec, tgt_v)
        if sec not in g.holding_age:
            g.holding_age[sec] = 0


def _derisk_holdings(context, bar_dict, equity_scale, cash_ratio):
    """二级/三级警戒：按持仓权重同比例压缩至目标权益比例（表5.3）."""
    acct = context.portfolio.stock_account
    total = acct.total_value
    held = _current_holdings(context)
    if total <= 0 or not held:
        return
    equity_ratio = (1.0 - cash_ratio) * equity_scale
    cur_equity = sum(acct.positions[s].market_value for s in held) / total
    if cur_equity <= equity_ratio + 0.02:
        return                                      # 已在目标以内
    scale = equity_ratio / cur_equity
    _logw('警戒降仓：权益 %.1f%% -> %.1f%%' % (
        cur_equity * 100, equity_ratio * 100))
    for sec in sorted(held):
        if not _is_tradable(context, bar_dict, sec, for_buy=False):
            continue
        pos = _position(acct, sec)
        if pos is None:
            continue
        order_target_value(sec, pos.market_value * scale)


def _dispose_flagged_holdings(context, bar_dict):
    """个股层面处置：持仓触发拥挤或极端波动即剔除（6.2.2）."""
    p = g.p
    held = _current_holdings(context)
    vol = pd.Series(g.vol_today).dropna()
    extreme = set()
    if len(vol) >= 20:
        pct = vol.rank(pct=True)
        extreme = set(pct[pct >= p['vol_exclude_pct']].index)
    to_drop = (g.crowded_today | extreme) & held
    for sec in sorted(to_drop):
        if _is_tradable(context, bar_dict, sec, for_buy=False):
            order_target_value(sec, 0)
            g.holding_age.pop(sec, None)
            _logw('风险处置卖出 %s（拥挤=%s 极端波动=%s）' % (
                sec, sec in g.crowded_today, sec in extreme))


# ==================================================== 盘中快速通道 ====
def _intraday_fast_channel(context, bar_dict):
    """分钟频率下的盘中监控（报告5.3）：回撤逼近警戒线即时响应."""
    nav = _total_value(context)
    d = g.dd
    if d['peak'] is None or d['peak'] <= 0:
        return
    dd = 1.0 - nav / max(d['peak'], nav)
    p = g.p
    # 盘中触及三级警戒：立即降仓+满额对冲，不等日终
    if dd >= p['dd_l3'] and d['level'] < 3:
        _logw('盘中触及三级警戒线（DD=%.2f%%），启动全面防守' % (dd * 100))
        d['level'] = 3
        _derisk_holdings(context, bar_dict, p['equity_scale_l3'],
                         max(1.0 - p['equity_scale_l3'], p['cash_risk']))
        _manage_hedge(context, 1.00)
        g.hedge['ratio'] = 1.00
    elif dd >= p['dd_l2'] and d['level'] < 2:
        _logw('盘中触及二级警戒线（DD=%.2f%%），降仓并上调对冲' % (dd * 100))
        d['level'] = 2
        _derisk_holdings(context, bar_dict, p['equity_scale_l2'], p['cash_risk'])
        ratio = max(g.hedge['ratio'], 0.60)
        _manage_hedge(context, ratio)
        g.hedge['ratio'] = ratio


# ======================================================== 盘后 ====
def after_trading(context):
    acct = context.portfolio.stock_account
    held = _current_holdings(context)
    equity = sum(acct.positions[s].market_value for s in held) if held else 0.0
    total = _total_value(context)
    log.info('[收盘] 总资产%.0f | 股票市值%.0f(%.1f%%) | 持仓%d只 | '
             'DD=%.2f%% L%d | 对冲%.0f%% | 防御=%s' % (
                 total, equity,
                 equity / acct.total_value * 100 if acct.total_value > 0 else 0.0,
                 len(held), g.dd['dd'] * 100, g.dd['level'],
                 g.hedge['ratio'] * 100, g.icmon['defensive']))


# ======================================================== 工具函数 ====
def _logw(msg):
    """部分平台版本无 log.warn，降级为 log.info."""
    try:
        log.warn(msg)
    except Exception:
        log.info('[WARN] ' + msg)


def _position(acct, sec):
    """安全读取持仓对象（positions可能不支持.get）."""
    try:
        if sec in acct.positions:
            return acct.positions[sec]
    except Exception:
        pass
    return None


def _total_value(context):
    """股票+期货账户总资产."""
    try:
        return (context.portfolio.stock_account.total_value
                + context.portfolio.future_account.total_value)
    except Exception:
        return context.portfolio.total_value


def _current_holdings(context):
    """当前股票持仓集合（数量>0）."""
    held = set()
    try:
        for sec, pos in context.portfolio.stock_account.positions.items():
            amt = getattr(pos, 'amount', None)
            if amt is None:
                amt = getattr(pos, 'total_amount', 0)
            if amt and amt > 0:
                held.add(sec)
    except Exception:
        pass
    return held


def _is_tradable(context, bar_dict, sec, for_buy=True):
    """可交易复核（表7.1）：停牌、一字涨跌停、ST剔除.

    买入方向额外规避涨停不可成交；卖出方向规避跌停不可成交。
    """
    # ST复核
    try:
        info = get_security_info(sec)
        name = getattr(info, 'display_name', '') or getattr(info, 'name', '')
        if 'ST' in name.upper():
            return False
    except Exception:
        pass

    try:
        bar = bar_dict[sec]
        # 停牌：无成交/无行情
        if bar is None:
            return False
        vol = getattr(bar, 'volume', None)
        is_paused = getattr(bar, 'is_paused', None)
        if is_paused:
            return False
        if vol is not None and vol <= 0:
            return False
        o = getattr(bar, 'open', None)
        h = getattr(bar, 'high', None)
        l = getattr(bar, 'low', None)
        if o is None:
            return True
        # 一字板判定 + 涨跌停方向判定（以±9.5%近似阈值，兼容非20%品种池）
        prev = getattr(bar, 'prev_close', None)
        if prev is None:
            try:
                hist = history(sec, ['close'], 2, '1d', False, 'pre', is_panel=0)
                prev = float(hist['close'].values[-2])
            except Exception:
                prev = None
        one_line = (h is not None and l is not None and h == l)
        if prev and prev > 0:
            chg = o / prev - 1.0
            if for_buy and one_line and chg > 0.095:
                return False                        # 一字涨停无法买入
            if (not for_buy) and one_line and chg < -0.095:
                return False                        # 一字跌停无法卖出
        elif one_line:
            return False
        return True
    except Exception:
        return False
