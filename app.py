import os
import re
import json
import math
import numpy as np
import pandas as pd

# 兼容 Excel 读取
# pip install pandas numpy openpyxl

def _safe_read_tablelike(path):
    ext = os.path.splitext(path)[1].lower()
    try:
        if ext in ['.xlsx', '.xls']:
            return pd.read_excel(path)
        elif ext == '.csv':
            return pd.read_csv(path)
        elif ext == '.json':
            with open(path, 'r', encoding='utf-8') as f:
                obj = json.load(f)
            # 允许 JSON 是列表或字典，尝试标准化为 DataFrame
            if isinstance(obj, list):
                return pd.json_normalize(obj)
            elif isinstance(obj, dict):
                # 若字典含有主键列表
                # 尝试寻找类似 records 的键
                for k, v in obj.items():
                    if isinstance(v, list):
                        return pd.json_normalize(v)
                # 否则拉平
                return pd.json_normalize(obj)
            else:
                return pd.DataFrame()
        else:
            return pd.DataFrame()
    except Exception:
        return pd.DataFrame()

def _standardize_month_str(dt_like):
    """
    将日期或字符串标准化为简洁的月标识，如：
    2026-10-01 -> 'Oct 26'
    2026-03 -> 'Mar 26'
    26-Mar -> '26-Mar'（如果原始数据已经类似格式则保留）
    """
    if pd.isna(dt_like):
        return None
    if isinstance(dt_like, pd.Timestamp):
        return dt_like.strftime('%b %y')
    if isinstance(dt_like, (np.datetime64, )):
        dt = pd.to_datetime(dt_like)
        return dt.strftime('%b %y')
    s = str(dt_like).strip()
    # 如果已经是 'Mon YY' 或 'YY-Mon' 的格式
    if re.match(r'^[A-Za-z]{3}\s+\d{2}$', s):
        return s
    if re.match(r'^\d{2}-[A-Za-z]{3}$', s):
        return s
    # 常见日期格式
    try:
        dt = pd.to_datetime(s, errors='raise', dayfirst=False)
        return dt.strftime('%b %y')
    except Exception:
        # 保留原样
        return s

def _month_sort_key(m):
    """
    将 'Oct 26' 或 '26-Oct' 统一转为排序键：YYYYMM
    假设年份为 20 + yy
    """
    if pd.isna(m):
        return 999999
    s = str(m)
    month_map = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                 'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
    # 'Mon YY'
    m1 = re.match(r'^([A-Za-z]{3})\s+(\d{2})$', s)
    if m1:
        mon = month_map.get(m1.group(1), 13)
        yy = int(m1.group(2))
        year = 2000 + yy
        return year * 100 + mon
    # 'YY-Mon'
    m2 = re.match(r'^(\d{2})-([A-Za-z]{3})$', s)
    if m2:
        yy = int(m2.group(1))
        mon = month_map.get(m2.group(2), 13)
        year = 2000 + yy
        return year * 100 + mon
    # 尝试解析一般日期
    try:
        dt = pd.to_datetime(s, errors='raise')
        return dt.year * 100 + dt.month
    except Exception:
        return 999999

def _weighted_price(values, weights):
    """
    计算加权价格，values/weights 均为向量。
    若权重和为0，返回 NaN。
    """
    values = np.array(values, dtype=float)
    weights = np.array(weights, dtype=float)
    total = weights.sum()
    if total == 0:
        return np.nan
    return float(np.dot(values, weights) / total)

def _build_paper_positions(source_df):
    """
    根据原始数据构造每月的 paper_pos 和 paper_neg 以及其价差（或价格）。
    你可以根据手头数据列名调整映射。这里做尽可能鲁棒的识别：

    假设数据可能包含：
    - month / 合约月份
    - paper_qty / hedge_qty / volume / position
    - price 或 price_diff（价差）
    - side（Buy/Sell或Long/Short，决定正负）
    """
    df = source_df.copy()
    # 尝试识别月份列
    month_cols = [c for c in df.columns if c.lower() in ['month','contract_month','period','mth']]
    if len(month_cols) == 0:
        # 尝试从 'date' 或 'trade_date'
        date_cols = [c for c in df.columns if c.lower() in ['date','trade_date','asof']]
        if len(date_cols):
            df['Month'] = df[date_cols[0]].apply(_standardize_month_str)
        else:
            df['Month'] = None
    else:
        df['Month'] = df[month_cols[0]].apply(_standardize_month_str)

    # 数量列
    qty_cols = [c for c in df.columns if c.lower() in ['qty','quantity','volume','hedge_qty','paper_qty','position','lot']]
    qty_col = qty_cols[0] if qty_cols else None

    # 价格列
    price_cols = [c for c in df.columns if 'price' in c.lower() or 'px' in c.lower() or 'diff' in c.lower()]
    price_col = price_cols[0] if price_cols else None

    # 方向列
    side_cols = [c for c in df.columns if c.lower() in ['side','buy_sell','direction','long_short']]
    side_col = side_cols[0] if side_cols else None

    # 清洗
    if qty_col is None:
        df['qty'] = 0.0
    else:
        df['qty'] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0.0)

    if price_col is None:
        df['price'] = np.nan
    else:
        df['price'] = pd.to_numeric(df[price_col], errors='coerce')

    # 定义正负
    # Buy/Long -> 正；Sell/Short -> 负；若无方向列，则根据 sign 列或默认为正
    if side_col is not None:
        side = df[side_col].astype(str).str.lower()
        sign = np.where(side.str.contains('sell') | side.str.contains('short'), -1, 1)
    else:
        # 若存在显式 sign 列
        sign_cols = [c for c in df.columns if 'sign' in c.lower()]
        if sign_cols:
            sign = pd.to_numeric(df[sign_cols[0]], errors='coerce').fillna(1.0)
            sign = np.where(sign < 0, -1, 1)
        else:
            sign = np.ones(len(df))

    df['signed_qty'] = df['qty'] * sign

    # 月度聚合：分别统计正负纸面量和加权价格
    # 正负分组数据
    pos_mask = df['signed_qty'] > 0
    neg_mask = df['signed_qty'] < 0

    # 聚合到月
    paper_pos = df[pos_mask].groupby('Month', dropna=False)['signed_qty'].sum()
    paper_neg = df[neg_mask].groupby('Month', dropna=False)['signed_qty'].sum()  # 为负数

    # 加权价格：按正负分别计算
    wp_pos = df[pos_mask].groupby('Month').apply(lambda g: _weighted_price(g['price'], g['signed_qty']))
    wp_neg = df[neg_mask].groupby('Month').apply(lambda g: _weighted_price(g['price'], np.abs(g['signed_qty'])))

    out = pd.DataFrame({
        'paper_pos': paper_pos,
        'paper_neg': paper_neg,
    }).reset_index()

    # 填充缺失
    out['paper_pos'] = out['paper_pos'].fillna(0.0)
    out['paper_neg'] = out['paper_neg'].fillna(0.0)
    # 对应加权价格
    out = out.merge(wp_pos.rename('weighted_price_positive').reset_index(), on='Month', how='left')
    out = out.merge(wp_neg.rename('weighted_price_negative').reset_index(), on='Month', how='left')
    return out

def _build_physical_net(source_df):
    """
    根据原始物理数据构造每月 physical_net。
    假设存在：
    - month 或 date 列
    - qty/volume/amount 列
    - inflow/outflow 或 type 来区分加减（可选）
    """
    df = source_df.copy()
    # 识别月份
    month_cols = [c for c in df.columns if c.lower() in ['month','contract_month','period','mth']]
    if len(month_cols) == 0:
        date_cols = [c for c in df.columns if c.lower() in ['date','trade_date','asof','delivery_date','ship_date']]
        if len(date_cols):
            df['Month'] = df[date_cols[0]].apply(_standardize_month_str)
        else:
            df['Month'] = None
    else:
        df['Month'] = df[month_cols[0]].apply(_standardize_month_str)

    qty_cols = [c for c in df.columns if c.lower() in ['qty','quantity','volume','net_qty','net','amount','mt','bbls']]
    qty_col = qty_cols[0] if qty_cols else None

    if qty_col is None:
        df['qty'] = 0.0
    else:
        df['qty'] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0.0)

    # inflow/outflow 或 type
    type_cols = [c for c in df.columns if c.lower() in ['type','flow','inout','direction']]
    if type_cols:
        t = df[type_cols[0]].astype(str).str.lower()
        sign = np.where(t.str.contains('out') | t.str.contains('sell') | t.str.contains('export'), -1, 1)
    else:
        # 若有显式 sign
        sign_cols = [c for c in df.columns if 'sign' in c.lower()]
        if sign_cols:
            sign = pd.to_numeric(df[sign_cols[0]], errors='coerce').fillna(1.0)
            sign = np.where(sign < 0, -1, 1)
        else:
            # 默认所有为正净量（如到港/生产）
            sign = np.ones(len(df))

    df['signed_qty'] = df['qty'] * sign

    physical_net = df.groupby('Month', dropna=False)['signed_qty'].sum().reset_index()
    physical_net = physical_net.rename(columns={'signed_qty':'physical_net'})
    return physical_net

def _match_physical_with_paper(month_df):
    """
    将 physical_net 与纸面正负进行匹配：
    - 先用 physical_net 对冲负纸面（paper_neg 为负数，匹配量为 min(physical_net, abs(paper_neg))）
    - 余量再考虑正纸面，但通常正纸面代表需要对冲的买入方向，这里示例不消耗 physical 去匹配正纸面，只记录正纸面的数量
    """
    df = month_df.copy()
    # 确保列存在
    for col in ['physical_net','paper_pos','paper_neg']:
        if col not in df.columns:
            df[col] = 0.0

    # 匹配负纸面
    df['matched_qty_against_negative_paper'] = np.minimum(df['physical_net'], np.abs(df['paper_neg']))
    # 简化：不匹配正纸面，仅记录为0
    df['matched_qty_against_positive_paper'] = 0

    return df

def _collect_candidate_files():
    """
    根据你的文件系统中列出的文件名，自动收集可能的物理与纸面数据文件。
    你也可以直接指定。
    """
    files = os.listdir('.')

    # 假定以下文件可能包含物理与纸面数据（依据你提供的文件列表）
    paper_candidates = [
        'position_report.csv',
        'natgas_position_report.csv',
        'filtered_hedge_allocation.csv',
        'hedge_allocation_v19_optimized.csv',
        'weighted_prices_by_commodity.csv',
        'monthly_contract_summary.csv',
    ]
    physical_candidates = [
        'physical_cargo_ledger.csv',
        'physical_cargo_ledger-20260224-073004.csv',
        'monthly_pl_reconciliation.csv',
        'trade.xlsx',
        'ticket0226.xlsx',
        '0224ticket.xlsx',
        '1229ticket.xlsx',
        'test ticket.csv',
        '20251027091452_ticket_data.xlsx',
        '20251126162114_ticket_data.xlsx',
    ]

    paper_files = [f for f in paper_candidates if f in files]
    physical_files = [f for f in physical_candidates if f in files]

    return paper_files, physical_files

def _ingest_paper():
    paper_files, _ = _collect_candidate_files()
    dfs = []
    for f in paper_files:
        df = _safe_read_tablelike(f)
        if not df.empty:
            df['__source__'] = f
            dfs.append(df)
    if len(dfs) == 0:
        return pd.DataFrame(columns=['Month','paper_pos','paper_neg','weighted_price_positive','weighted_price_negative'])
    raw = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return _build_paper_positions(raw)

def _ingest_physical():
    _, physical_files = _collect_candidate_files()
    dfs = []
    for f in physical_files:
        df = _safe_read_tablelike(f)
        if not df.empty:
            df['__source__'] = f
            dfs.append(df)
    if len(dfs) == 0:
        return pd.DataFrame(columns=['Month','physical_net'])
    raw = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return _build_physical_net(raw)

def run_workflow():
    """
    主工作流：读取数据 -> 构造纸面与物理 -> 合并 -> 匹配 -> 排序输出
    """
    paper_month = _ingest_paper()
    physical_month = _ingest_physical()

    # 合并
    merged = pd.merge(physical_month, paper_month, on='Month', how='outer')
    for col in ['physical_net','paper_pos','paper_neg','weighted_price_positive','weighted_price_negative']:
        if col not in merged.columns:
            merged[col] = 0.0 if 'weighted_price' not in col else np.nan

    # 填充与类型统一
    merged['physical_net'] = merged['physical_net'].fillna(0.0)
    merged['paper_pos'] = merged['paper_pos'].fillna(0.0)
    merged['paper_neg'] = merged['paper_neg'].fillna(0.0)
    # 负纸面确保为负
    merged['paper_neg'] = np.where(merged['paper_neg'] > 0, -merged['paper_neg'], merged['paper_neg'])

    # 匹配逻辑
    matched = _match_physical_with_paper(merged)

    # 排序并美化月份显示：优先按月序
    matched['__sort__'] = matched['Month'].apply(_month_sort_key)
    matched = matched.sort_values('__sort__', kind='mergesort').drop(columns='__sort__')

    # 将 'Month' 两种格式合一：如果是 'YY-Mon' 则转为 'Mon YY' 显示
    def normalize_month_display(s):
        if pd.isna(s):
            return s
        st = str(s)
        m2 = re.match(r'^(\d{2})-([A-Za-z]{3})$', st)
        if m2:
            yy = m2.group(1)
            mon = m2.group(2)
            return f'{mon} {yy}'
        return st

    matched['Month'] = matched['Month'].apply(normalize_month_display)

    # 与示例输出列顺序一致
    cols = [
        'Month',
        'physical_net',
        'paper_pos',
        'paper_neg',
        'matched_qty_against_negative_paper',
        'matched_qty_against_positive_paper',
        'weighted_price_negative',
        'weighted_price_positive',
    ]
    for c in cols:
        if c not in matched.columns:
            matched[c] = np.nan if 'weighted_price' in c else 0.0

    final = matched[cols].copy()

    # 数值格式整理
    for c in ['physical_net','paper_pos','paper_neg',
              'matched_qty_against_negative_paper','matched_qty_against_positive_paper']:
        final[c] = pd.to_numeric(final[c], errors='coerce').fillna(0.0)

    # 返回 DataFrame
    return final

# 允许直接运行此文件以快速查看结果
if __name__ == '__main__':
    df = run_workflow()
    print(df)