import os
import re
import json
import numpy as np
import pandas as pd

# 兼容 Excel 读取
# pip install pandas numpy openpyxl streamlit

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
                for _, v in obj.items():
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


def _safe_read_uploaded_tablelike(uploaded_file):
    """兼容 Streamlit 上传文件读取。"""
    ext = os.path.splitext(uploaded_file.name)[1].lower()
    try:
        if ext in ['.xlsx', '.xls']:
            return pd.read_excel(uploaded_file)
        if ext == '.csv':
            return pd.read_csv(uploaded_file)
        if ext == '.json':
            obj = json.load(uploaded_file)
            if isinstance(obj, list):
                return pd.json_normalize(obj)
            if isinstance(obj, dict):
                for _, v in obj.items():
                    if isinstance(v, list):
                        return pd.json_normalize(v)
                return pd.json_normalize(obj)
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
    month_map = {'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
                 'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12}
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
    """
    df = source_df.copy()
    month_cols = [c for c in df.columns if c.lower() in ['month', 'contract_month', 'period', 'mth']]
    if len(month_cols) == 0:
        date_cols = [c for c in df.columns if c.lower() in ['date', 'trade_date', 'asof']]
        if len(date_cols):
            df['Month'] = df[date_cols[0]].apply(_standardize_month_str)
        else:
            df['Month'] = None
    else:
        df['Month'] = df[month_cols[0]].apply(_standardize_month_str)

    qty_cols = [c for c in df.columns if c.lower() in ['qty', 'quantity', 'volume', 'hedge_qty', 'paper_qty', 'position', 'lot']]
    qty_col = qty_cols[0] if qty_cols else None

    price_cols = [c for c in df.columns if 'price' in c.lower() or 'px' in c.lower() or 'diff' in c.lower()]
    price_col = price_cols[0] if price_cols else None

    side_cols = [c for c in df.columns if c.lower() in ['side', 'buy_sell', 'direction', 'long_short']]
    side_col = side_cols[0] if side_cols else None

    if qty_col is None:
        df['qty'] = 0.0
    else:
        df['qty'] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0.0)

    if price_col is None:
        df['price'] = np.nan
    else:
        df['price'] = pd.to_numeric(df[price_col], errors='coerce')

    if side_col is not None:
        side = df[side_col].astype(str).str.lower()
        sign = np.where(side.str.contains('sell') | side.str.contains('short'), -1, 1)
    else:
        sign_cols = [c for c in df.columns if 'sign' in c.lower()]
        if sign_cols:
            sign = pd.to_numeric(df[sign_cols[0]], errors='coerce').fillna(1.0)
            sign = np.where(sign < 0, -1, 1)
        else:
            sign = np.ones(len(df))

    df['signed_qty'] = df['qty'] * sign

    pos_mask = df['signed_qty'] > 0
    neg_mask = df['signed_qty'] < 0

    paper_pos = df[pos_mask].groupby('Month', dropna=False)['signed_qty'].sum()
    paper_neg = df[neg_mask].groupby('Month', dropna=False)['signed_qty'].sum()

    wp_pos = df[pos_mask].groupby('Month').apply(lambda g: _weighted_price(g['price'], g['signed_qty']))
    wp_neg = df[neg_mask].groupby('Month').apply(lambda g: _weighted_price(g['price'], np.abs(g['signed_qty'])))

    out = pd.DataFrame({
        'paper_pos': paper_pos,
        'paper_neg': paper_neg,
    }).reset_index()

    out['paper_pos'] = out['paper_pos'].fillna(0.0)
    out['paper_neg'] = out['paper_neg'].fillna(0.0)
    out = out.merge(wp_pos.rename('weighted_price_positive').reset_index(), on='Month', how='left')
    out = out.merge(wp_neg.rename('weighted_price_negative').reset_index(), on='Month', how='left')
    return out


def _build_physical_net(source_df):
    """根据原始物理数据构造每月 physical_net。"""
    df = source_df.copy()
    month_cols = [c for c in df.columns if c.lower() in ['month', 'contract_month', 'period', 'mth']]
    if len(month_cols) == 0:
        date_cols = [c for c in df.columns if c.lower() in ['date', 'trade_date', 'asof', 'delivery_date', 'ship_date']]
        if len(date_cols):
            df['Month'] = df[date_cols[0]].apply(_standardize_month_str)
        else:
            df['Month'] = None
    else:
        df['Month'] = df[month_cols[0]].apply(_standardize_month_str)

    qty_cols = [c for c in df.columns if c.lower() in ['qty', 'quantity', 'volume', 'net_qty', 'net', 'amount', 'mt', 'bbls']]
    qty_col = qty_cols[0] if qty_cols else None

    if qty_col is None:
        df['qty'] = 0.0
    else:
        df['qty'] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0.0)

    type_cols = [c for c in df.columns if c.lower() in ['type', 'flow', 'inout', 'direction']]
    if type_cols:
        t = df[type_cols[0]].astype(str).str.lower()
        sign = np.where(t.str.contains('out') | t.str.contains('sell') | t.str.contains('export'), -1, 1)
    else:
        sign_cols = [c for c in df.columns if 'sign' in c.lower()]
        if sign_cols:
            sign = pd.to_numeric(df[sign_cols[0]], errors='coerce').fillna(1.0)
            sign = np.where(sign < 0, -1, 1)
        else:
            sign = np.ones(len(df))

    df['signed_qty'] = df['qty'] * sign

    physical_net = df.groupby('Month', dropna=False)['signed_qty'].sum().reset_index()
    physical_net = physical_net.rename(columns={'signed_qty': 'physical_net'})
    return physical_net


def _match_physical_with_paper(month_df):
    """将 physical_net 与纸面正负进行匹配。"""
    df = month_df.copy()
    for col in ['physical_net', 'paper_pos', 'paper_neg']:
        if col not in df.columns:
            df[col] = 0.0

    df['matched_qty_against_negative_paper'] = np.minimum(df['physical_net'], np.abs(df['paper_neg']))
    df['matched_qty_against_positive_paper'] = 0

    return df


def _collect_candidate_files():
    files = os.listdir('.')

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


def _ingest_paper(uploaded_paper_files=None):
    paper_files, _ = _collect_candidate_files()
    dfs = []
    if uploaded_paper_files:
        for uploaded in uploaded_paper_files:
            df = _safe_read_uploaded_tablelike(uploaded)
            if not df.empty:
                df['__source__'] = uploaded.name
                dfs.append(df)
    else:
        for f in paper_files:
            df = _safe_read_tablelike(f)
            if not df.empty:
                df['__source__'] = f
                dfs.append(df)

    if len(dfs) == 0:
        return pd.DataFrame(columns=['Month', 'paper_pos', 'paper_neg', 'weighted_price_positive', 'weighted_price_negative'])
    raw = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return _build_paper_positions(raw)


def _ingest_physical(uploaded_physical_files=None):
    _, physical_files = _collect_candidate_files()
    dfs = []
    if uploaded_physical_files:
        for uploaded in uploaded_physical_files:
            df = _safe_read_uploaded_tablelike(uploaded)
            if not df.empty:
                df['__source__'] = uploaded.name
                dfs.append(df)
    else:
        for f in physical_files:
            df = _safe_read_tablelike(f)
            if not df.empty:
                df['__source__'] = f
                dfs.append(df)

    if len(dfs) == 0:
        return pd.DataFrame(columns=['Month', 'physical_net'])
    raw = pd.concat(dfs, ignore_index=True) if dfs else pd.DataFrame()
    return _build_physical_net(raw)


def run_workflow(uploaded_paper_files=None, uploaded_physical_files=None):
    """主工作流：读取数据 -> 构造纸面与物理 -> 合并 -> 匹配 -> 排序输出"""
    paper_month = _ingest_paper(uploaded_paper_files=uploaded_paper_files)
    physical_month = _ingest_physical(uploaded_physical_files=uploaded_physical_files)

    merged = pd.merge(physical_month, paper_month, on='Month', how='outer')
    for col in ['physical_net', 'paper_pos', 'paper_neg', 'weighted_price_positive', 'weighted_price_negative']:
        if col not in merged.columns:
            merged[col] = 0.0 if 'weighted_price' not in col else np.nan

    merged['physical_net'] = merged['physical_net'].fillna(0.0)
    merged['paper_pos'] = merged['paper_pos'].fillna(0.0)
    merged['paper_neg'] = merged['paper_neg'].fillna(0.0)
    merged['paper_neg'] = np.where(merged['paper_neg'] > 0, -merged['paper_neg'], merged['paper_neg'])

    matched = _match_physical_with_paper(merged)

    matched['__sort__'] = matched['Month'].apply(_month_sort_key)
    matched = matched.sort_values('__sort__', kind='mergesort').drop(columns='__sort__')

    def normalize_month_display(s):
        if pd.isna(s):
            return s
        st_value = str(s)
        m2 = re.match(r'^(\d{2})-([A-Za-z]{3})$', st_value)
        if m2:
            yy = m2.group(1)
            mon = m2.group(2)
            return f'{mon} {yy}'
        return st_value

    matched['Month'] = matched['Month'].apply(normalize_month_display)

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

    for c in ['physical_net', 'paper_pos', 'paper_neg',
              'matched_qty_against_negative_paper', 'matched_qty_against_positive_paper']:
        final[c] = pd.to_numeric(final[c], errors='coerce').fillna(0.0)

    return final


def _streamlit_app():
    import streamlit as st
    st.set_page_config(page_title='HEDGE ENGINE', layout='wide')
    st.title('HEDGE ENGINE - Streamlit 部署版')
    st.caption('保留原有计算逻辑；支持上传文件或直接读取当前目录候选文件。')

    with st.sidebar:
        st.header('数据输入')
        uploaded_paper_files = st.file_uploader(
            '上传纸面对冲数据（可多选）',
            type=['csv', 'xlsx', 'xls', 'json'],
            accept_multiple_files=True,
        )
        uploaded_physical_files = st.file_uploader(
            '上传物理数据（可多选）',
            type=['csv', 'xlsx', 'xls', 'json'],
            accept_multiple_files=True,
        )
        run_btn = st.button('运行计算', type='primary')

    if run_btn:
        result = run_workflow(
            uploaded_paper_files=uploaded_paper_files,
            uploaded_physical_files=uploaded_physical_files,
        )
        st.subheader('结果表')
        st.dataframe(result, use_container_width=True)
        st.download_button(
            label='下载结果 CSV',
            data=result.to_csv(index=False).encode('utf-8-sig'),
            file_name='hedge_engine_result.csv',
            mime='text/csv',
        )
    else:
        st.info('请在左侧上传文件后点击“运行计算”。如果不上传文件，将读取当前目录中的默认候选文件。')


if __name__ == '__main__':
    _streamlit_app()
