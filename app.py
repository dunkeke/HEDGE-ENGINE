import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import os
import re
import json
from datetime import datetime
import io

# 设置页面配置
st.set_page_config(
    page_title="纸货匹配与Ticket透视系统",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# 自定义CSS样式
st.markdown("""
<style>
    .stApp {
        background: linear-gradient(180deg, #f8fbff 0%, #f2f7ff 52%, #eef4ff 100%);
        color: #0f172a;
    }
    .main-header {
        font-size: 2.4rem;
        color: #0D47A1;
        text-align: center;
        margin-bottom: 0.8rem;
        letter-spacing: 0.4px;
        font-weight: 700;
    }
    .sub-header {
        font-size: 1.4rem;
        color: #0B5394;
        margin-top: 0.8rem;
        margin-bottom: 0.4rem;
        font-weight: 600;
    }
    .hero-panel {
        background: linear-gradient(120deg, #0b3d91 0%, #1565c0 55%, #1e88e5 100%);
        color: #ffffff;
        border-radius: 14px;
        padding: 1.1rem 1.2rem;
        margin: 0.4rem 0 1rem 0;
        box-shadow: 0 8px 22px rgba(13,71,161,.18);
    }
    .hero-panel p {
        margin: 0.25rem 0 0 0;
        opacity: .92;
    }
    .metric-card {
        background: #ffffff;
        border-radius: 12px;
        padding: 16px;
        box-shadow: 0 6px 20px rgba(15, 23, 42, 0.08);
        border: 1px solid #d7e3f7;
        min-height: 140px;
    }
    .metric-card h3 {
        margin-bottom: 0.45rem;
        color: #0b3d91;
        font-weight: 700;
    }
    .metric-card p {
        margin: 0;
        color: #1f2937;
        line-height: 1.45;
    }
    .info-text {
        color: #1f2937;
        font-size: 0.95rem;
    }
    .stAlert {
        border-radius: 10px;
    }
    .stMarkdown, .stText, .stCaption, label, p {
        color: #0f172a;
    }
    [data-testid="stSidebar"] {
        background: #eaf2ff;
    }
    [data-testid="stSidebar"] * {
        color: #0f172a !important;
    }
    [data-testid="stSidebar"] .stRadio > div {
        background: #ffffff;
        border-radius: 10px;
        padding: 6px;
        border: 1px solid #dde8fb;
    }
</style>
""", unsafe_allow_html=True)

# 初始化session state
if 'paper_df' not in st.session_state:
    st.session_state.paper_df = None
if 'physical_df' not in st.session_state:
    st.session_state.physical_df = None
if 'matched_df' not in st.session_state:
    st.session_state.matched_df = None
if 'ticket_df' not in st.session_state:
    st.session_state.ticket_df = None
if 'ticket_pivot' not in st.session_state:
    st.session_state.ticket_pivot = None

# 工具函数（从原代码继承并增强）

def safe_read_tablelike(uploaded_file):
    """安全读取上传的文件"""
    if uploaded_file is None:
        return pd.DataFrame()
    
    file_extension = os.path.splitext(uploaded_file.name)[1].lower()
    try:
        if file_extension in ['.xlsx', '.xls']:
            return pd.read_excel(uploaded_file)
        elif file_extension == '.csv':
            return pd.read_csv(uploaded_file)
        elif file_extension == '.json':
            return pd.read_json(uploaded_file)
        else:
            return pd.DataFrame()
    except Exception as e:
        st.error(f"读取文件 {uploaded_file.name} 时出错: {str(e)}")
        return pd.DataFrame()

def standardize_month_str(dt_like):
    """标准化月份字符串"""
    if pd.isna(dt_like):
        return None
    if isinstance(dt_like, pd.Timestamp):
        return dt_like.strftime('%b %y')
    if isinstance(dt_like, (np.datetime64,)):
        dt = pd.to_datetime(dt_like)
        return dt.strftime('%b %y')
    s = str(dt_like).strip()
    # 如果已经是 'Mon YY' 格式
    if re.match(r'^[A-Za-z]{3}\s+\d{2}$', s):
        return s
    if re.match(r'^\d{2}-[A-Za-z]{3}$', s):
        return s
    try:
        dt = pd.to_datetime(s, errors='raise', dayfirst=False)
        return dt.strftime('%b %y')
    except Exception:
        return s

def month_sort_key(m):
    """月份排序键"""
    if pd.isna(m):
        return 999999
    s = str(m)
    month_map = {'Jan':1,'Feb':2,'Mar':3,'Apr':4,'May':5,'Jun':6,
                 'Jul':7,'Aug':8,'Sep':9,'Oct':10,'Nov':11,'Dec':12}
    m1 = re.match(r'^([A-Za-z]{3})\s+(\d{2})$', s)
    if m1:
        mon = month_map.get(m1.group(1), 13)
        yy = int(m1.group(2))
        year = 2000 + yy
        return year * 100 + mon
    m2 = re.match(r'^(\d{2})-([A-Za-z]{3})$', s)
    if m2:
        yy = int(m2.group(1))
        mon = month_map.get(m2.group(2), 13)
        year = 2000 + yy
        return year * 100 + mon
    try:
        dt = pd.to_datetime(s, errors='raise')
        return dt.year * 100 + dt.month
    except Exception:
        return 999999

def normalize_month_key(month_series):
    """统一Month键的类型，避免merge时因dtype不一致报错"""
    return month_series.apply(standardize_month_str).astype('string')

def weighted_price(values, weights):
    """计算加权价格"""
    values = np.array(values, dtype=float)
    weights = np.array(weights, dtype=float)
    total = weights.sum()
    if total == 0:
        return np.nan
    return float(np.dot(values, weights) / total)

def build_paper_positions(source_df):
    """构建纸面头寸数据"""
    df = source_df.copy()
    if df.empty:
        return pd.DataFrame(columns=['Month','paper_pos','paper_neg','weighted_price_positive','weighted_price_negative'])
    
    # 识别月份列
    month_cols = [c for c in df.columns if c.lower() in ['month','contract_month','period','mth']]
    if len(month_cols) == 0:
        date_cols = [c for c in df.columns if c.lower() in ['date','trade_date','asof']]
        if len(date_cols):
            df['Month'] = df[date_cols[0]].apply(standardize_month_str)
        else:
            df['Month'] = None
    else:
        df['Month'] = df[month_cols[0]].apply(standardize_month_str)

    df['Month'] = normalize_month_key(df['Month'])

    # 数量列
    qty_cols = [c for c in df.columns if c.lower() in ['qty','quantity','volume','hedge_qty','paper_qty','position','lot']]
    qty_col = qty_cols[0] if qty_cols else None

    # 价格列
    price_cols = [c for c in df.columns if 'price' in c.lower() or 'px' in c.lower() or 'diff' in c.lower()]
    price_col = price_cols[0] if price_cols else None

    # 方向列
    side_cols = [c for c in df.columns if c.lower() in ['side','buy_sell','direction','long_short']]
    side_col = side_cols[0] if side_cols else None

    # 清洗数据
    if qty_col is None:
        df['qty'] = 0.0
    else:
        df['qty'] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0.0)

    if price_col is None:
        df['price'] = np.nan
    else:
        df['price'] = pd.to_numeric(df[price_col], errors='coerce')

    # 定义正负方向
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

    # 月度聚合
    pos_mask = df['signed_qty'] > 0
    neg_mask = df['signed_qty'] < 0

    paper_pos = df[pos_mask].groupby('Month', dropna=False)['signed_qty'].sum()
    paper_neg = df[neg_mask].groupby('Month', dropna=False)['signed_qty'].sum()

    wp_pos = df[pos_mask].groupby('Month').apply(lambda g: weighted_price(g['price'], g['signed_qty']))
    wp_neg = df[neg_mask].groupby('Month').apply(lambda g: weighted_price(g['price'], np.abs(g['signed_qty'])))

    out = pd.DataFrame({
        'paper_pos': paper_pos,
        'paper_neg': paper_neg,
    }).reset_index()

    out['paper_pos'] = out['paper_pos'].fillna(0.0)
    out['paper_neg'] = out['paper_neg'].fillna(0.0)
    out = out.merge(wp_pos.rename('weighted_price_positive').reset_index(), on='Month', how='left')
    out = out.merge(wp_neg.rename('weighted_price_negative').reset_index(), on='Month', how='left')
    
    return out

def build_physical_net(source_df):
    """构建物理净头寸数据"""
    df = source_df.copy()
    if df.empty:
        return pd.DataFrame(columns=['Month','physical_net'])
    
    # 识别月份
    month_cols = [c for c in df.columns if c.lower() in ['month','contract_month','period','mth']]
    if len(month_cols) == 0:
        date_cols = [c for c in df.columns if c.lower() in ['date','trade_date','asof','delivery_date','ship_date']]
        if len(date_cols):
            df['Month'] = df[date_cols[0]].apply(standardize_month_str)
        else:
            df['Month'] = None
    else:
        df['Month'] = df[month_cols[0]].apply(standardize_month_str)

    df['Month'] = normalize_month_key(df['Month'])

    qty_cols = [c for c in df.columns if c.lower() in ['qty','quantity','volume','net_qty','net','amount','mt','bbls']]
    qty_col = qty_cols[0] if qty_cols else None

    if qty_col is None:
        df['qty'] = 0.0
    else:
        df['qty'] = pd.to_numeric(df[qty_col], errors='coerce').fillna(0.0)

    # 处理流入流出方向
    type_cols = [c for c in df.columns if c.lower() in ['type','flow','inout','direction']]
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
    physical_net = physical_net.rename(columns={'signed_qty':'physical_net'})
    return physical_net

def match_physical_with_paper(month_df):
    """匹配物理与纸面数据"""
    df = month_df.copy()
    for col in ['physical_net','paper_pos','paper_neg']:
        if col not in df.columns:
            df[col] = 0.0

    df['matched_qty_against_negative_paper'] = np.minimum(df['physical_net'], np.abs(df['paper_neg']))
    df['matched_qty_against_positive_paper'] = 0
    df['unmatched_physical'] = df['physical_net'] - df['matched_qty_against_negative_paper']
    df['unmatched_paper_neg'] = np.abs(df['paper_neg']) - df['matched_qty_against_negative_paper']
    df['unmatched_paper_pos'] = df['paper_pos']

    return df

def create_ticket_pivot(ticket_df):
    """创建Ticket透视表"""
    if ticket_df.empty:
        return pd.DataFrame()
    
    df = ticket_df.copy()
    
    # 尝试识别关键列
    date_cols = [c for c in df.columns if 'date' in c.lower() or 'time' in c.lower()]
    if date_cols:
        df['Date'] = pd.to_datetime(df[date_cols[0]], errors='coerce')
        df['Month'] = df['Date'].apply(lambda x: x.strftime('%b %y') if pd.notna(x) else None)
        df['Week'] = df['Date'].apply(lambda x: x.strftime('%Y-W%W') if pd.notna(x) else None)
        df['Day'] = df['Date'].apply(lambda x: x.strftime('%Y-%m-%d') if pd.notna(x) else None)
    
    # 识别数量列
    qty_cols = [c for c in df.columns if 'qty' in c.lower() or 'volume' in c.lower() or 'amount' in c.lower()]
    qty_col = qty_cols[0] if qty_cols else None
    
    # 识别价格列
    price_cols = [c for c in df.columns if 'price' in c.lower() or 'rate' in c.lower()]
    price_col = price_cols[0] if price_cols else None
    
    # 识别产品/合约列
    product_cols = [c for c in df.columns if 'product' in c.lower() or 'commodity' in c.lower() or 'contract' in c.lower()]
    product_col = product_cols[0] if product_cols else None
    
    # 创建透视表
    pivot_data = []
    
    if 'Month' in df.columns and qty_col:
        monthly_sum = df.groupby('Month')[qty_col].sum().reset_index()
        monthly_sum['Period'] = 'Month'
        monthly_sum['Value'] = monthly_sum[qty_col]
        pivot_data.append(monthly_sum[['Month', 'Period', 'Value']])
    
    if 'Week' in df.columns and qty_col:
        weekly_sum = df.groupby('Week')[qty_col].sum().reset_index()
        weekly_sum['Period'] = 'Week'
        weekly_sum['Value'] = weekly_sum[qty_col]
        pivot_data.append(weekly_sum[['Week', 'Period', 'Value']].rename(columns={'Week': 'Month'}))
    
    if 'Day' in df.columns and qty_col:
        daily_sum = df.groupby('Day')[qty_col].sum().reset_index()
        daily_sum['Period'] = 'Day'
        daily_sum['Value'] = daily_sum[qty_col]
        pivot_data.append(daily_sum[['Day', 'Period', 'Value']].rename(columns={'Day': 'Month'}))
    
    if product_col and qty_col:
        product_sum = df.groupby(product_col)[qty_col].sum().reset_index()
        product_sum['Period'] = 'Product'
        product_sum['Value'] = product_sum[qty_col]
        pivot_data.append(product_sum[[product_col, 'Period', 'Value']].rename(columns={product_col: 'Month'}))
    
    if pivot_data:
        pivot_df = pd.concat(pivot_data, ignore_index=True)
        return pivot_df
    else:
        return df.head(100)  # 返回前100行作为预览

# 主应用
def main():
    st.markdown('<h1 class="main-header">📊 纸货匹配与Ticket透视系统</h1>', unsafe_allow_html=True)
    st.markdown("""
    <div class="hero-panel">
        <strong>一站式风险与头寸看板</strong>
        <p>上传原始文件后即可进行纸货匹配、Ticket透视与趋势分析，帮助更快定位敞口与匹配效率。</p>
    </div>
    """, unsafe_allow_html=True)
    
    # 侧边栏
    with st.sidebar:
        st.image("https://img.icons8.com/color/96/000000/combo-chart--v1.png", width=100)
        st.markdown("## 导航菜单")
        
        menu_options = ["🏠 首页", "📈 纸货匹配", "🎫 Ticket透视", "📊 数据分析", "⚙️ 设置"]
        choice = st.radio("选择功能", menu_options)
        
        st.markdown("---")
        st.markdown("### 文件上传区")
        
        uploaded_files = st.file_uploader(
            "上传数据文件",
            type=['csv', 'xlsx', 'xls', 'json'],
            accept_multiple_files=True
        )
        
        if uploaded_files:
            st.success(f"已上传 {len(uploaded_files)} 个文件")
            for f in uploaded_files:
                st.caption(f"✅ {f.name}")
        
        st.markdown("---")
        st.markdown("### 关于")
        st.info("此应用用于纸货头寸匹配和Ticket数据透视分析")
    
    # 主内容区
    if choice == "🏠 首页":
        show_home_page()
    elif choice == "📈 纸货匹配":
        show_paper_matching_page(uploaded_files)
    elif choice == "🎫 Ticket透视":
        show_ticket_pivot_page(uploaded_files)
    elif choice == "📊 数据分析":
        show_data_analysis_page()
    elif choice == "⚙️ 设置":
        show_settings_page()

def show_home_page():
    """首页"""
    st.markdown('<p class="info-text">欢迎使用分析平台：建议先在左侧上传纸面/物理或Ticket文件，再进入对应模块处理。</p>', unsafe_allow_html=True)
    col1, col2, col3 = st.columns(3)
    
    with col1:
        st.markdown("""
        <div class="metric-card">
            <h3>📈 纸货匹配</h3>
            <p>自动识别并匹配纸面头寸与物理头寸，计算加权价格和匹配量</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col2:
        st.markdown("""
        <div class="metric-card">
            <h3>🎫 Ticket透视</h3>
            <p>对水单ticket数据进行多维度透视分析，按月/周/日汇总</p>
        </div>
        """, unsafe_allow_html=True)
    
    with col3:
        st.markdown("""
        <div class="metric-card">
            <h3>📊 数据可视化</h3>
            <p>生成交互式图表，直观展示头寸分布和匹配情况</p>
        </div>
        """, unsafe_allow_html=True)
    
    st.markdown("---")
    st.markdown("### 快速开始")
    st.write("1. 在左侧边栏上传您的数据文件")
    st.write("2. 选择纸货匹配或Ticket透视功能")
    st.write("3. 系统将自动识别并处理数据")
    
    # 示例数据说明
    with st.expander("查看支持的数据格式"):
        st.markdown("""
        **纸面数据应包含：**
        - 月份列 (month/contract_month/date)
        - 数量列 (qty/volume/position)
        - 价格列 (price/px/diff)
        - 方向列 (side/buy_sell) - 可选
        
        **物理数据应包含：**
        - 月份列 (month/delivery_date)
        - 数量列 (qty/volume/net)
        - 流向列 (type/flow) - 可选
        
        **Ticket数据应包含：**
        - 日期列 (date/time)
        - 数量列 (qty/volume)
        - 产品/合约列 (product/commodity) - 可选
        """)

def show_paper_matching_page(uploaded_files):
    """纸货匹配页面"""
    st.markdown('<h2 class="sub-header">📈 纸货匹配分析</h2>', unsafe_allow_html=True)
    
    if not uploaded_files:
        st.warning("请先在侧边栏上传数据文件")
        return
    
    # 文件分类
    paper_files = []
    physical_files = []
    
    for f in uploaded_files:
        # 简单的文件名分类逻辑
        if any(keyword in f.name.lower() for keyword in ['paper', 'hedge', 'position', 'contract']):
            paper_files.append(f)
        elif any(keyword in f.name.lower() for keyword in ['physical', 'cargo', 'ledger', 'trade']):
            physical_files.append(f)
        else:
            # 默认让用户选择
            if st.checkbox(f"将 {f.name} 作为纸面数据?", key=f.name):
                paper_files.append(f)
            else:
                physical_files.append(f)
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("### 📄 纸面数据文件")
        for f in paper_files:
            st.write(f"✓ {f.name}")
    
    with col2:
        st.markdown("### 📦 物理数据文件")
        for f in physical_files:
            st.write(f"✓ {f.name}")
    
    if st.button("🚀 运行匹配分析", type="primary"):
        with st.spinner("正在处理数据..."):
            # 处理纸面数据
            paper_dfs = []
            for f in paper_files:
                df = safe_read_tablelike(f)
                if not df.empty:
                    paper_dfs.append(df)
            
            if paper_dfs:
                paper_raw = pd.concat(paper_dfs, ignore_index=True)
                st.session_state.paper_df = build_paper_positions(paper_raw)
            
            # 处理物理数据
            physical_dfs = []
            for f in physical_files:
                df = safe_read_tablelike(f)
                if not df.empty:
                    physical_dfs.append(df)
            
            if physical_dfs:
                physical_raw = pd.concat(physical_dfs, ignore_index=True)
                st.session_state.physical_df = build_physical_net(physical_raw)
            
            # 合并匹配
            if st.session_state.paper_df is not None and st.session_state.physical_df is not None:
                st.session_state.paper_df['Month'] = normalize_month_key(st.session_state.paper_df['Month'])
                st.session_state.physical_df['Month'] = normalize_month_key(st.session_state.physical_df['Month'])

                merged = pd.merge(
                    st.session_state.physical_df,
                    st.session_state.paper_df,
                    on='Month',
                    how='outer'
                )
                
                # 填充缺失值
                for col in ['physical_net', 'paper_pos', 'paper_neg']:
                    if col in merged.columns:
                        merged[col] = merged[col].fillna(0.0)
                
                if 'paper_neg' in merged.columns:
                    merged['paper_neg'] = np.where(merged['paper_neg'] > 0, -merged['paper_neg'], merged['paper_neg'])
                
                st.session_state.matched_df = match_physical_with_paper(merged)
                
                # 排序
                st.session_state.matched_df['__sort__'] = st.session_state.matched_df['Month'].apply(month_sort_key)
                st.session_state.matched_df = st.session_state.matched_df.sort_values('__sort__').drop(columns='__sort__')
                
                st.success("✅ 匹配完成！")
    
    # 显示结果
    if st.session_state.matched_df is not None:
        st.markdown("---")
        st.subheader("📊 匹配结果")
        
        # 数据显示选项
        view_option = st.radio("查看方式", ["表格", "图表"], horizontal=True)
        
        if view_option == "表格":
            # 格式化显示
            display_df = st.session_state.matched_df.copy()
            for col in ['physical_net', 'paper_pos', 'paper_neg', 
                       'matched_qty_against_negative_paper', 'unmatched_physical']:
                if col in display_df.columns:
                    display_df[col] = display_df[col].round(2)
            
            st.dataframe(display_df, use_container_width=True)
            
            # 下载按钮
            csv = display_df.to_csv(index=False).encode('utf-8')
            st.download_button(
                "📥 下载匹配结果 (CSV)",
                csv,
                "paper_matching_results.csv",
                "text/csv",
                key='download-csv'
            )
        
        else:
            # 图表展示
            fig = make_subplots(
                rows=2, cols=2,
                subplot_titles=('头寸分布', '匹配情况', '未匹配头寸', '加权价格'),
                specs=[[{"secondary_y": True}, {"secondary_y": False}],
                       [{"secondary_y": False}, {"secondary_y": False}]]
            )
            
            df_plot = st.session_state.matched_df.dropna(subset=['Month'])
            
            # 头寸分布
            fig.add_trace(
                go.Bar(name='物理净头寸', x=df_plot['Month'], y=df_plot['physical_net']),
                row=1, col=1
            )
            fig.add_trace(
                go.Bar(name='纸面正头寸', x=df_plot['Month'], y=df_plot['paper_pos']),
                row=1, col=1
            )
            fig.add_trace(
                go.Bar(name='纸面负头寸', x=df_plot['Month'], y=df_plot['paper_neg']),
                row=1, col=1
            )
            
            # 匹配情况
            fig.add_trace(
                go.Bar(name='匹配量', x=df_plot['Month'], y=df_plot['matched_qty_against_negative_paper']),
                row=1, col=2
            )
            
            # 未匹配头寸
            fig.add_trace(
                go.Bar(name='未匹配物理', x=df_plot['Month'], y=df_plot['unmatched_physical']),
                row=2, col=1
            )
            fig.add_trace(
                go.Bar(name='未匹配纸面负', x=df_plot['Month'], y=df_plot['unmatched_paper_neg']),
                row=2, col=1
            )
            
            # 加权价格
            fig.add_trace(
                go.Scatter(name='正头寸价格', x=df_plot['Month'], y=df_plot['weighted_price_positive'],
                          mode='lines+markers'),
                row=2, col=2
            )
            fig.add_trace(
                go.Scatter(name='负头寸价格', x=df_plot['Month'], y=df_plot['weighted_price_negative'],
                          mode='lines+markers'),
                row=2, col=2
            )
            
            fig.update_layout(height=800, showlegend=True)
            fig.update_xaxes(tickangle=45)
            st.plotly_chart(fig, use_container_width=True)

def show_ticket_pivot_page(uploaded_files):
    """Ticket透视页面"""
    st.markdown('<h2 class="sub-header">🎫 Ticket透视分析</h2>', unsafe_allow_html=True)
    
    if not uploaded_files:
        st.warning("请先在侧边栏上传数据文件")
        return
    
    # 选择要分析的ticket文件
    ticket_files = [f for f in uploaded_files if 'ticket' in f.name.lower()]
    
    if not ticket_files:
        ticket_files = uploaded_files  # 如果没有明确标识，使用所有文件
    
    selected_files = st.multiselect(
        "选择要分析的Ticket文件",
        [f.name for f in ticket_files],
        default=[f.name for f in ticket_files[:2]] if len(ticket_files) > 1 else [f.name for f in ticket_files]
    )
    
    if st.button("🔍 生成透视表", type="primary"):
        with st.spinner("正在生成透视表..."):
            ticket_dfs = []
            for f in ticket_files:
                if f.name in selected_files:
                    df = safe_read_tablelike(f)
                    if not df.empty:
                        df['_source_file'] = f.name
                        ticket_dfs.append(df)
            
            if ticket_dfs:
                st.session_state.ticket_df = pd.concat(ticket_dfs, ignore_index=True)
                st.session_state.ticket_pivot = create_ticket_pivot(st.session_state.ticket_df)
                st.success("✅ 透视表生成完成！")
    
    # 显示透视结果
    if st.session_state.ticket_pivot is not None:
        st.markdown("---")
        
        # 透视选项
        pivot_type = st.selectbox(
            "选择透视维度",
            ["按月汇总", "按周汇总", "按日汇总", "按产品汇总", "原始数据"]
        )
        
        if pivot_type == "按月汇总" and 'Month' in st.session_state.ticket_df.columns:
            monthly = st.session_state.ticket_df.groupby('Month').agg({
                col: 'sum' for col in st.session_state.ticket_df.columns 
                if 'qty' in col.lower() or 'volume' in col.lower() or 'amount' in col.lower()
            }).reset_index()
            st.dataframe(monthly, use_container_width=True)
            
        elif pivot_type == "按周汇总" and 'Week' in st.session_state.ticket_df.columns:
            weekly = st.session_state.ticket_df.groupby('Week').agg({
                col: 'sum' for col in st.session_state.ticket_df.columns 
                if 'qty' in col.lower() or 'volume' in col.lower() or 'amount' in col.lower()
            }).reset_index()
            st.dataframe(weekly, use_container_width=True)
            
        elif pivot_type == "原始数据":
            st.dataframe(st.session_state.ticket_df, use_container_width=True)
            
        else:
            st.dataframe(st.session_state.ticket_pivot, use_container_width=True)
        
        # 数据可视化
        st.markdown("---")
        st.subheader("📈 数据趋势")
        
        if 'Month' in st.session_state.ticket_df.columns:
            # 识别数值列
            numeric_cols = st.session_state.ticket_df.select_dtypes(include=[np.number]).columns.tolist()
            
            if numeric_cols:
                y_col = st.selectbox("选择要显示的数值列", numeric_cols)
                
                fig = px.line(
                    st.session_state.ticket_df.groupby('Month')[y_col].sum().reset_index(),
                    x='Month',
                    y=y_col,
                    title=f"{y_col} 月度趋势"
                )
                fig.update_xaxes(tickangle=45)
                st.plotly_chart(fig, use_container_width=True)

def show_data_analysis_page():
    """数据分析页面"""
    st.markdown('<h2 class="sub-header">📊 数据分析</h2>', unsafe_allow_html=True)
    
    tab1, tab2, tab3 = st.tabs(["头寸分析", "匹配分析", "价格分析"])
    
    with tab1:
        if st.session_state.matched_df is not None:
            df = st.session_state.matched_df
            
            # 关键指标
            col1, col2, col3, col4 = st.columns(4)
            
            with col1:
                st.metric("总物理头寸", f"{df['physical_net'].sum():,.0f}")
            with col2:
                st.metric("总纸面正头寸", f"{df['paper_pos'].sum():,.0f}")
            with col3:
                st.metric("总纸面负头寸", f"{abs(df['paper_neg'].sum()):,.0f}")
            with col4:
                st.metric("总匹配量", f"{df['matched_qty_against_negative_paper'].sum():,.0f}")
            
            # 分布图
            fig = px.bar(
                df.melt(id_vars=['Month'], 
                       value_vars=['physical_net', 'paper_pos', 'paper_neg'],
                       var_name='头寸类型', value_name='数量'),
                x='Month',
                y='数量',
                color='头寸类型',
                title="头寸分布",
                barmode='group'
            )
            st.plotly_chart(fig, use_container_width=True)
    
    with tab2:
        if st.session_state.matched_df is not None:
            df = st.session_state.matched_df
            
            # 匹配效率
            df['match_rate'] = df['matched_qty_against_negative_paper'] / abs(df['paper_neg'].replace(0, np.nan)) * 100
            
            fig = make_subplots(specs=[[{"secondary_y": True}]])
            
            fig.add_trace(
                go.Bar(name='匹配量', x=df['Month'], y=df['matched_qty_against_negative_paper']),
                secondary_y=False
            )
            
            fig.add_trace(
                go.Scatter(name='匹配率(%)', x=df['Month'], y=df['match_rate'],
                          mode='lines+markers'),
                secondary_y=True
            )
            
            fig.update_layout(title="匹配效率分析")
            st.plotly_chart(fig, use_container_width=True)
    
    with tab3:
        if st.session_state.matched_df is not None:
            df = st.session_state.matched_df
            
            if 'weighted_price_positive' in df.columns and 'weighted_price_negative' in df.columns:
                fig = go.Figure()
                
                fig.add_trace(go.Scatter(
                    x=df['Month'],
                    y=df['weighted_price_positive'],
                    name='正头寸价格',
                    mode='lines+markers'
                ))
                
                fig.add_trace(go.Scatter(
                    x=df['Month'],
                    y=df['weighted_price_negative'],
                    name='负头寸价格',
                    mode='lines+markers'
                ))
                
                fig.update_layout(title="加权价格趋势")
                st.plotly_chart(fig, use_container_width=True)

def show_settings_page():
    """设置页面"""
    st.markdown('<h2 class="sub-header">⚙️ 系统设置</h2>', unsafe_allow_html=True)
    
    st.subheader("数据列映射配置")
    
    col1, col2 = st.columns(2)
    
    with col1:
        st.markdown("**纸面数据列映射**")
        paper_month_col = st.text_input("月份列", value="month")
        paper_qty_col = st.text_input("数量列", value="qty")
        paper_price_col = st.text_input("价格列", value="price")
        paper_side_col = st.text_input("方向列", value="side")
    
    with col2:
        st.markdown("**物理数据列映射**")
        physical_month_col = st.text_input("月份列", value="delivery_date")
        physical_qty_col = st.text_input("数量列", value="volume")
        physical_flow_col = st.text_input("流向列", value="type")
    
    if st.button("保存设置"):
        st.success("设置已保存")
    
    st.markdown("---")
    st.subheader("系统信息")
    st.info(f"当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    st.info(f"Pandas版本: {pd.__version__}")
    st.info(f"Numpy版本: {np.__version__}")

if __name__ == "__main__":
    main()
