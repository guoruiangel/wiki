#!/usr/bin/env python3
"""
OpenClaw 评分管理系统 - Flask Web 应用
支持评分记录、定时任务监控、Token消耗统计
"""

from flask import Flask, render_template, request, jsonify, redirect, url_for, Response
import sqlite3
import json
import urllib.request
import urllib.error

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

_no_redirect_opener = urllib.request.build_opener(_NoRedirect)

import random
from datetime import datetime, timedelta
import os
from typing import Dict, List, Any, Optional
from network_scanner import NetworkScanner
from data_stats_api import get_token_stats, get_token_trend_data, get_token_records, get_optimization_tips, get_budget_status
from openclaw_cron_integration import get_cron_manager

app = Flask(__name__)
app.secret_key = 'openclaw-secret-key-2026'
# 本地日报服务代理配置
LOCAL_REPORTS_URL = "http://192.168.1.83:5001"

def proxy_reports(path):
    """代理请求到本地日报服务"""
    url = f"{LOCAL_REPORTS_URL}{path}"
    try:
        req = urllib.request.Request(url)
        if request.query_string:
            url = f"{url}?{request.query_string.decode()}"
            req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.read(), resp.status, resp.headers.get('Content-Type', 'text/html')
    except urllib.error.HTTPError as e:
        return f"Proxy error: {e}".encode(), e.code, 'text/plain'
    except Exception as e:
        return f"Proxy error: {e}".encode(), 502, 'text/plain'



# Wiki 代理配置
WIKI_URL = "http://192.168.1.83:5010"

import urllib.error

def proxy_wiki(path):
    """代理请求到本地 Wiki 服务"""
    url = f"{WIKI_URL}{path}"
    try:
        body = request.get_data()
        ctype = request.headers.get("Content-Type", "application/x-www-form-urlencoded")
        
        req = urllib.request.Request(url, data=body or None, method=request.method)
        req.add_header("Content-Type", ctype)
        cookie = request.headers.get("Cookie")
        if cookie:
            req.add_header("Cookie", cookie)
        
        if request.query_string:
            url = f"{url}?{request.query_string.decode()}"
            req2 = urllib.request.Request(url, data=body or None, method=request.method)
            req2.add_header("Content-Type", ctype)
            if cookie:
                req2.add_header("Cookie", cookie)
            req = req2
        
        with _no_redirect_opener.open(req, timeout=30) as resp:
            content = resp.read()
            # 修复wiki左侧导航bug：kk渲染的 navigatePage(test1) 缺引号，test1被视为变量
            # 用正则给slug补上引号: navigatePage(test1) -> navigatePage('test1')
            # 注入修复脚本：覆盖onclick重写为data-slug触发
            import re as _re
            FIX_SCRIPT = b'''<script>
var _origNav = window.navigatePage;
window.navigatePage = function(slug) {
    if (typeof slug === 'string' && slug.indexOf("'") === -1) {
        _origNav(slug);
    }
};
document.addEventListener('click', function(e) {
    var item = e.target.closest('.tree-item');
    if (item && item.dataset.slug) {
        e.preventDefault();
        e.stopPropagation();
        _origNav(item.dataset.slug);
    }
}, true);
</script>'''
            content = content.replace(b'</body>', FIX_SCRIPT + b'</body>')
            return Response(content, status=resp.status, headers=dict(resp.headers))
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, headers=dict(e.headers))
    except Exception as e:
        import traceback
        return Response(f"Wiki proxy error: {e}\n{traceback.format_exc()}", status=502, mimetype="text/plain")

@app.route("/wiki/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
@app.route("/wiki/<path:path>", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
def wiki_proxy(path):
    """Wiki 页面代理"""
    if path:
        target = f"/wiki/{path}"
    else:
        target = "/wiki/"
    return proxy_wiki(target)


app.debug = False

# 请求前检查数据库
@app.before_request
def before_request():
    """请求前检查数据库表是否存在"""
    try:
        conn = get_db_connection()
        # 检查关键表是否存在
        conn.execute("SELECT 1 FROM scores LIMIT 1")
        conn.close()
    except sqlite3.OperationalError:
        # 表不存在，初始化数据库
        try:
            init_database()
            scan_and_init_modules()
            print("✅ 数据库表已自动创建")
        except Exception as e:
            print(f"❌ 数据库初始化失败: {e}")

# 数据库配置
DB_PATH = os.path.join(os.path.dirname(__file__), 'scores.db')

def init_database():
    """初始化数据库"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 创建评分表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS scores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        year_month VARCHAR(7) NOT NULL,
        week_number INTEGER NOT NULL,
        score_change INTEGER NOT NULL,
        reason TEXT NOT NULL,
        category VARCHAR(50) NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # 创建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_year_month ON scores(year_month)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_category ON scores(category)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_created_at ON scores(created_at)')
    
    # 插入示例数据（如果表为空）
    cursor.execute('SELECT COUNT(*) FROM scores')
    if cursor.fetchone()[0] == 0:
        sample_data = [
            ('2026-03', 4, -2, '19:50承诺未兑现', '时间管理'),
            ('2026-03', 4, -2, '19:35承诺未兑现', '时间管理'),
            ('2026-03', 4, -2, '卡点未及时汇报', '沟通'),
            ('2026-03', 4, 2, '精简回答进步', '沟通'),
            ('2026-03', 4, 1, '记住重要信息', '记忆'),
            ('2026-03', 4, 1, '学习精简说话', '沟通'),
        ]
        cursor.executemany(
            'INSERT INTO scores (year_month, week_number, score_change, reason, category) VALUES (?, ?, ?, ?, ?)',
            sample_data
        )
    
    # 创建网络扫描定时设置表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS network_schedule (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        enabled BOOLEAN DEFAULT 1,
        schedule_expr VARCHAR(50) DEFAULT '30 18 * * *',
        schedule_tz VARCHAR(50) DEFAULT 'Asia/Shanghai',
        description TEXT DEFAULT '每日网络设备扫描',
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # 创建模块复用统计表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS code_modules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        module_name VARCHAR(100) NOT NULL UNIQUE,
        module_type VARCHAR(50) NOT NULL,  -- 'core', 'api', 'script', 'utility'
        file_path VARCHAR(255) NOT NULL,
        lines_of_code INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        reuse_count INTEGER DEFAULT 0,
        estimated_tokens_saved INTEGER DEFAULT 0,
        description TEXT
    )
    ''')
    
    # 创建模块使用记录表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS module_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        module_id INTEGER NOT NULL,
        usage_context VARCHAR(100) NOT NULL,  -- 'development', 'cron', 'api_call', 'manual'
        tokens_estimated_saved INTEGER DEFAULT 0,
        usage_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        notes TEXT DEFAULT '',
        FOREIGN KEY (module_id) REFERENCES code_modules (id)
    )
    ''')
    
    # 创建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_module_name ON code_modules(module_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_module_type ON code_modules(module_type)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_module_usage ON module_usage(module_id, usage_timestamp)')
    
    # 创建评分规则表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS scoring_rules (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_name VARCHAR(100) NOT NULL,  -- '扣分规则', '加分规则', '中性规则'
        min_value DECIMAL(10,2),          -- 最小值（单位：M tokens）
        max_value DECIMAL(10,2),          -- 最大值（单位：M tokens）
        score INTEGER NOT NULL,           -- 分数变化（正数为加分，负数为扣分）
        description TEXT,                 -- 规则描述
        is_active BOOLEAN DEFAULT 1,      -- 是否启用
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    ''')
    
    # 创建评分规则顺序表
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS scoring_rule_order (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_id INTEGER NOT NULL,
        display_order INTEGER DEFAULT 0,
        FOREIGN KEY (rule_id) REFERENCES scoring_rules (id)
    )
    ''')
    
    # 创建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rule_name ON scoring_rules(rule_name)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rule_active ON scoring_rules(is_active)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_rule_order ON scoring_rule_order(display_order)')
    
    # 插入默认评分规则（如果表为空）
    cursor.execute('SELECT COUNT(*) FROM scoring_rules')
    if cursor.fetchone()[0] == 0:
        default_rules = [
            ('扣分规则', 10.0, None, -10, '日消耗 > 10M tokens', 1),
            ('扣分规则', 8.0, 10.0, -5, '日消耗 8-10M tokens', 1),
            ('扣分规则', 5.0, 8.0, -3, '日消耗 5-8M tokens', 1),
            ('扣分规则', 3.0, 5.0, -1, '日消耗 3-5M tokens', 1),
            ('扣分规则', 1.0, 3.0, 0, '日消耗 1-3M tokens', 1),
        ]
        
        for i, rule in enumerate(default_rules):
            cursor.execute('''
                INSERT INTO scoring_rules (rule_name, min_value, max_value, score, description, is_active)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', rule)
            
            rule_id = cursor.lastrowid
            cursor.execute('''
                INSERT INTO scoring_rule_order (rule_id, display_order)
                VALUES (?, ?)
            ''', (rule_id, i))
    
    # 创建token每日消耗表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS token_daily_consumption (
            date TEXT PRIMARY KEY,
            total_tokens INTEGER NOT NULL,
            development_tokens INTEGER DEFAULT 0,
            search_tokens INTEGER DEFAULT 0,
            communication_tokens INTEGER DEFAULT 0,
            other_tokens INTEGER DEFAULT 0,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建每日评分表
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS daily_scores (
            date TEXT PRIMARY KEY,
            token_consumption INTEGER DEFAULT 0,
            score INTEGER DEFAULT 0,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    # 创建索引
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_token_date ON token_daily_consumption(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_token_total ON token_daily_consumption(total_tokens)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_score_date ON daily_scores(date)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_score_value ON daily_scores(score)')
    
    # 插入一些示例数据（如果表为空）
    cursor.execute('SELECT COUNT(*) FROM token_daily_consumption')
    if cursor.fetchone()[0] == 0:
        # 插入最近7天的示例数据
        today = datetime.now().date()
        sample_token_data = []
        sample_score_data = []
        
        for i in range(7):
            date = (today - timedelta(days=i)).strftime('%Y-%m-%d')
            # 生成随机的token消耗数据
            total_tokens = random.randint(500000, 3000000)  # 0.5M - 3M tokens
            dev_tokens = int(total_tokens * 0.7)   # 开发：70%
            comm_tokens = int(total_tokens * 0.25) # 沟通：25%
            other_tokens = int(total_tokens * 0.05) # 其他：5%
            search_tokens = 0                       # 搜索：0%
            
            sample_token_data.append((
                date, total_tokens, dev_tokens, search_tokens, 
                comm_tokens, other_tokens, f'示例数据 {date}'
            ))
            
            # 根据token消耗计算分数
            total_m = total_tokens / 1000000
            if total_m > 10:
                score = -10
                reason = '日消耗 > 10M tokens'
            elif total_m > 8:
                score = -5
                reason = '日消耗 8-10M tokens'
            elif total_m > 5:
                score = -3
                reason = '日消耗 5-8M tokens'
            elif total_m > 3:
                score = -1
                reason = '日消耗 3-5M tokens'
            elif total_m > 1:
                score = 0
                reason = '日消耗 1-3M tokens'
            else:
                score = 1
                reason = '日消耗 < 1M tokens'
            
            sample_score_data.append((
                date, total_tokens, score, reason
            ))
        
        cursor.executemany('''
            INSERT INTO token_daily_consumption 
            (date, total_tokens, development_tokens, search_tokens, communication_tokens, other_tokens, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', sample_token_data)
        
        cursor.executemany('''
            INSERT INTO daily_scores 
            (date, token_consumption, score, reason)
            VALUES (?, ?, ?, ?)
        ''', sample_score_data)
    
    conn.commit()
    conn.close()

def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

@app.route('/')
def index():
    """主页面"""
    conn = get_db_connection()
    
    # 获取所有评分记录（按年月-周排序）
    scores = conn.execute('SELECT * FROM scores ORDER BY year_month DESC, week_number DESC, created_at DESC').fetchall()
    
    # 计算统计信息
    total_score = conn.execute('SELECT SUM(score_change) FROM scores').fetchone()[0] or 0
    total_records = len(scores)
    
    # 正负分计数
    positive_count = conn.execute(
        'SELECT COUNT(*) FROM scores WHERE score_change > 0'
    ).fetchone()[0]
    
    negative_count = conn.execute(
        'SELECT COUNT(*) FROM scores WHERE score_change < 0'
    ).fetchone()[0]
    
    # 类别统计
    categories = conn.execute(
        'SELECT category, COUNT(*) as count FROM scores GROUP BY category ORDER BY count DESC'
    ).fetchall()
    
    # 月度统计
    monthly_stats = conn.execute('''
        SELECT year_month, SUM(score_change) as total, COUNT(*) as count
        FROM scores 
        GROUP BY year_month 
        ORDER BY year_month DESC
    ''').fetchall()
    
    # 平均变化
    avg_change = conn.execute('SELECT AVG(score_change) FROM scores').fetchone()[0] or 0
    
    # 最高最低分
    max_positive = conn.execute(
        'SELECT MAX(score_change) FROM scores WHERE score_change > 0'
    ).fetchone()[0] or 0
    
    min_negative = conn.execute(
        'SELECT MIN(score_change) FROM scores WHERE score_change < 0'
    ).fetchone()[0] or 0
    
    # 计算评分频率（假设从第一条记录开始）
    if scores:
        first_date = datetime.strptime(scores[-1]['created_at'][:10], '%Y-%m-%d')
        last_date = datetime.strptime(scores[0]['created_at'][:10], '%Y-%m-%d')
        days_diff = (last_date - first_date).days + 1
        frequency = days_diff / total_records if total_records > 0 else 0
    else:
        frequency = 0
    
    # 当前年月
    current_month = datetime.now().strftime('%Y-%m')
    
    # 本月统计
    current_month_stats = conn.execute(
        'SELECT SUM(score_change), COUNT(*) FROM scores WHERE year_month = ?',
        (current_month,)
    ).fetchone()
    current_month_total = current_month_stats[0] or 0
    current_month_count = current_month_stats[1] or 0
    
    # 本周统计（假设周一开始）
    week_start = datetime.now().strftime('%Y-%m-%d')
    week_stats = conn.execute(
        'SELECT SUM(score_change), COUNT(*) FROM scores WHERE created_at >= ?',
        (week_start,)
    ).fetchone()
    week_total = week_stats[0] or 0
    week_count = week_stats[1] or 0
    
    # 最近7天统计
    seven_days_ago = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    seven_days_stats = conn.execute(
        'SELECT SUM(score_change), COUNT(*) FROM scores WHERE created_at >= ?',
        (seven_days_ago,)
    ).fetchone()
    seven_days_total = seven_days_stats[0] or 0
    seven_days_count = seven_days_stats[1] or 0
    
    # 最常见的扣分类别
    top_negative_categories = conn.execute('''
        SELECT category, COUNT(*) as count 
        FROM scores 
        WHERE score_change < 0 
        GROUP BY category 
        ORDER BY count DESC 
        LIMIT 3
    ''').fetchall()
    
    conn.close()
    
    return render_template('index.html',
                         scores=scores,
                         total_score=total_score,
                         total_records=total_records,
                         positive_count=positive_count,
                         negative_count=negative_count,
                         categories=categories,
                         monthly_stats=monthly_stats,
                         average_change=round(avg_change, 2),
                         max_positive=max_positive,
                         min_negative=min_negative,
                         frequency=round(frequency, 1),
                         current_month=current_month,
                         current_month_total=current_month_total,
                         current_month_count=current_month_count,
                         week_total=week_total,
                         week_count=week_count,
                         seven_days_total=seven_days_total,
                         seven_days_count=seven_days_count,
                         top_negative_categories=top_negative_categories)

@app.route('/api/scores', methods=['GET'])
def get_scores_api():
    """获取评分记录API"""
    conn = get_db_connection()
    
    # 支持分页和筛选
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 1000, type=int)  # 默认返回所有记录
    category = request.args.get('category')
    year_month = request.args.get('year_month')
    
    # 先获取总记录数
    count_query = 'SELECT COUNT(*) FROM scores'
    count_params = []
    
    conditions = []
    if category:
        conditions.append('category = ?')
        count_params.append(category)
    if year_month:
        conditions.append('year_month = ?')
        count_params.append(year_month)
    
    if conditions:
        count_query += ' WHERE ' + ' AND '.join(conditions)
    
    total_count = conn.execute(count_query, count_params).fetchone()[0]
    
    # 查询数据
    query = 'SELECT * FROM scores'
    params = []
    
    if conditions:
        query += ' WHERE ' + ' AND '.join(conditions)
    
    query += ' ORDER BY year_month DESC, week_number DESC, created_at DESC LIMIT ? OFFSET ?'
    params.extend([per_page, (page - 1) * per_page])
    
    scores = conn.execute(query, params).fetchall()
    
    # 转换为字典列表
    result = []
    for score in scores:
        result.append(dict(score))
    
    conn.close()
    
    return jsonify({
        'success': True,
        'data': result,
        'page': page,
        'per_page': per_page,
        'total': total_count,
        'total_records': len(result)
    })

@app.route('/api/scores', methods=['POST'])
def add_score_api():
    """添加评分记录API"""
    data = request.get_json()
    
    # 验证数据
    required_fields = ['year_month', 'week_number', 'score_change', 'reason', 'category']
    for field in required_fields:
        if field not in data:
            return jsonify({'success': False, 'error': f'缺少字段: {field}'}), 400
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT INTO scores (year_month, week_number, score_change, reason, category)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            data['year_month'],
            int(data['week_number']),
            int(data['score_change']),
            data['reason'],
            data['category']
        ))
        
        score_id = cursor.lastrowid
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': '评分记录添加成功',
            'score_id': score_id
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scores/<int:score_id>', methods=['PUT'])
def update_score_api(score_id):
    """更新评分记录API"""
    data = request.get_json()
    
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 构建更新语句
        update_fields = []
        params = []
        
        if 'year_month' in data:
            update_fields.append('year_month = ?')
            params.append(data['year_month'])
        
        if 'week_number' in data:
            update_fields.append('week_number = ?')
            params.append(int(data['week_number']))
        
        if 'score_change' in data:
            update_fields.append('score_change = ?')
            params.append(int(data['score_change']))
        
        if 'reason' in data:
            update_fields.append('reason = ?')
            params.append(data['reason'])
        
        if 'category' in data:
            update_fields.append('category = ?')
            params.append(data['category'])
        
        if not update_fields:
            return jsonify({'success': False, 'error': '没有可更新的字段'}), 400
        
        params.append(score_id)
        
        query = f'UPDATE scores SET {", ".join(update_fields)} WHERE id = ?'
        cursor.execute(query, params)
        
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'error': '记录不存在'}), 404
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': '评分记录更新成功'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/scores/<int:score_id>', methods=['DELETE'])
def delete_score_api(score_id):
    """删除评分记录API"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('DELETE FROM scores WHERE id = ?', (score_id,))
        
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'error': '记录不存在'}), 404
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': '评分记录删除成功'
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/stats')
def get_stats_api():
    """获取统计信息API"""
    conn = get_db_connection()
    
    # 基础统计
    total_score = conn.execute('SELECT SUM(score_change) FROM scores').fetchone()[0] or 0
    total_records = conn.execute('SELECT COUNT(*) FROM scores').fetchone()[0]
    
    # 月度趋势
    monthly_trend = conn.execute('''
        SELECT year_month, SUM(score_change) as total, COUNT(*) as count
        FROM scores 
        GROUP BY year_month 
        ORDER BY year_month
    ''').fetchall()
    
    # 类别分布
    category_dist = conn.execute('''
        SELECT category, COUNT(*) as count, SUM(score_change) as total
        FROM scores 
        GROUP BY category 
        ORDER BY count DESC
    ''').fetchall()
    
    # 周统计
    week_stats = conn.execute('''
        SELECT week_number, COUNT(*) as count, SUM(score_change) as total
        FROM scores 
        GROUP BY week_number 
        ORDER BY week_number
    ''').fetchall()
    
    conn.close()
    
    return jsonify({
        'success': True,
        'data': {
            'total_score': total_score,
            'total_records': total_records,
            'monthly_trend': [dict(row) for row in monthly_trend],
            'category_dist': [dict(row) for row in category_dist],
            'week_stats': [dict(row) for row in week_stats]
        }
    })

@app.route('/api/tasks')
def get_tasks_api():
    """获取定时任务信息API"""
    # 模拟定时任务数据
    tasks = [
        {
            'id': 1,
            'name': '新闻推送',
            'schedule': '每天 10:00',
            'status': 'normal',
            'last_run': '2026-03-27 10:07',
            'log_file': 'news_20260327_100733.log',
            'description': '计算机/AI/大模型/互联网新闻'
        },
        {
            'id': 2,
            'name': 'A股/港股预测',
            'schedule': '每天 14:00',
            'status': 'normal',
            'last_run': '2026-03-27 14:00',
            'log_file': 'stocks_20260327_140000.log',
            'description': '中芯国际、美团、腾讯等股票预测'
        },
        {
            'id': 3,
            'name': '美股预测',
            'schedule': '每天 22:30',
            'status': 'pending',
            'last_run': '2026-03-26 22:30',
            'log_file': 'stocks_20260326_223000.log',
            'description': '英伟达、特斯拉、苹果等美股预测'
        },
        {
            'id': 4,
            'name': 'Web仪表板开发',
            'schedule': '即时任务',
            'status': 'in_progress',
            'last_run': '2026-03-27 20:30',
            'log_file': '开发中',
            'description': 'Python Flask Web系统开发'
        }
    ]
    
    return jsonify({
        'success': True,
        'data': tasks
    })

@app.route('/api/tokens')
def get_tokens_api():
    """获取Token消耗统计API"""
    # 模拟Token数据
    token_stats = {
        'today': 1440,
        'total': 1440,
        'current_score': -7,
        'active_tasks': 3,
        'details': [
            {'time': '20:30', 'task': 'Python Web开发', 'consumed': 70, 'accumulated': 1440},
            {'time': '20:16', 'task': '状态保存', 'consumed': 60, 'accumulated': 1370},
            {'time': '20:15', 'task': '进展汇报', 'consumed': 70, 'accumulated': 1310},
            {'time': '20:07', 'task': '需求确认', 'consumed': 70, 'accumulated': 1240},
            {'time': '20:05', 'task': 'Python重建', 'consumed': 50, 'accumulated': 1170}
        ]
    }
    
    return jsonify({
        'success': True,
        'data': token_stats
    })

# ==================== 数据统计分析API ====================

@app.route('/data-stats')
def data_stats_page():
    """Token消耗录入与分析页面（简单版）"""
    return render_template('token_input_analysis.html')

@app.route('/data-test')
def data_test_page():
    """数据测试页面"""
    return render_template('data_test.html')

@app.route('/api/tokens/record', methods=['POST'])
def api_tokens_record():
    """记录Token消耗数据（支持自动估算分类）"""
    try:
        data = request.json
        
        # 验证必要字段
        required_fields = ['date', 'total_tokens']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'status': 'error',
                    'message': f'缺少必要字段: {field}'
                }), 400
        
        total_tokens = data['total_tokens']
        
        # 自动估算分类（如果未提供或不全）
        dev_tokens = data.get('development_tokens', 0)
        search_tokens = data.get('search_tokens', 0)
        comm_tokens = data.get('communication_tokens', 0)
        other_tokens = data.get('other_tokens', 0)
        
        # 如果分类数据不全，自动估算
        if total_tokens > 0 and (dev_tokens + search_tokens + comm_tokens + other_tokens) != total_tokens:
            # 基于历史数据分析的默认比例
            dev_tokens = int(total_tokens * 0.70)   # 开发：70%
            comm_tokens = int(total_tokens * 0.25)  # 沟通：25%
            other_tokens = int(total_tokens * 0.05) # 其他：5%
            search_tokens = 0                       # 搜索：0%
        
        # 计算分数（根据最新规则）
        total_m = total_tokens / 1000000
        score, reason = calculate_token_score(total_m)
        
        # 连接数据库
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 创建或更新token_daily_consumption表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS token_daily_consumption (
                date TEXT PRIMARY KEY,
                total_tokens INTEGER NOT NULL,
                development_tokens INTEGER DEFAULT 0,
                search_tokens INTEGER DEFAULT 0,
                communication_tokens INTEGER DEFAULT 0,
                other_tokens INTEGER DEFAULT 0,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 插入或更新数据
        cursor.execute('''
            INSERT OR REPLACE INTO token_daily_consumption 
            (date, total_tokens, development_tokens, search_tokens, communication_tokens, other_tokens, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['date'],
            total_tokens,
            dev_tokens,
            search_tokens,
            comm_tokens,
            other_tokens,
            data.get('notes', '')
        ))
        
        # 创建或更新daily_scores表
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS daily_scores (
                date TEXT PRIMARY KEY,
                token_consumption INTEGER NOT NULL,
                score INTEGER NOT NULL,
                reason TEXT,
                calculated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # 插入或更新分数记录
        cursor.execute('''
            INSERT OR REPLACE INTO daily_scores 
            (date, token_consumption, score, reason)
            VALUES (?, ?, ?, ?)
        ''', (
            data['date'],
            total_tokens,
            score,
            reason
        ))
        
        # 将token消耗扣分录入到scores表（如果分数不为0）
        if score != 0:
            # 获取当前年月和周数
            record_date = datetime.strptime(data['date'], '%Y-%m-%d')
            year_month = record_date.strftime('%Y-%m')
            week_number = calculate_week_number_by_date(record_date)
            
            # 检查是否已存在相同日期的token消耗记录
            cursor.execute('''
                SELECT COUNT(*) FROM scores 
                WHERE year_month = ? AND week_number = ? 
                AND reason LIKE ? AND category = 'Token消耗'
            ''', (year_month, week_number, f'%{data["date"]}%'))
            
            existing_count = cursor.fetchone()[0]
            
            # 如果不存在相同记录，则插入新记录
            if existing_count == 0:
                cursor.execute('''
                    INSERT INTO scores (year_month, week_number, score_change, reason, category)
                    VALUES (?, ?, ?, ?, ?)
                ''', (
                    year_month,
                    week_number,
                    score,
                    f'{data["date"]} token消耗{total_m:.2f}M，{reason}',
                    'Token消耗'
                ))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Token消耗数据保存成功',
            'data': {
                'date': data['date'],
                'total_tokens': total_tokens,
                'development_tokens': dev_tokens,
                'search_tokens': search_tokens,
                'communication_tokens': comm_tokens,
                'other_tokens': other_tokens,
                'score': score,
                'reason': reason,
                'auto_estimated': data.get('development_tokens', 0) == 0,  # 标记是否自动估算
                'recorded_at': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'保存失败: {str(e)}'
        }), 500

def calculate_token_score(total_m):
    """根据Token消耗计算分数（从数据库读取规则）"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 获取所有活跃的评分规则，按显示顺序排序
        cursor.execute('''
            SELECT sr.min_value, sr.max_value, sr.score, sr.description
            FROM scoring_rules sr
            LEFT JOIN scoring_rule_order sro ON sr.id = sro.rule_id
            WHERE sr.is_active = 1
            ORDER BY sro.display_order ASC
        ''')
        
        rules = cursor.fetchall()
        conn.close()
        
        # 如果没有规则，使用默认规则
        if not rules:
            return calculate_token_score_default(total_m)
        
        # 根据规则计算分数
        for min_val, max_val, score, description in rules:
            if max_val is None:  # 大于某个值
                if total_m > min_val:
                    return score, description
            elif min_val is None:  # 小于某个值
                if total_m < max_val:
                    return score, description
            else:  # 区间
                if min_val <= total_m < max_val:
                    return score, description
        
        # 如果没有匹配的规则，使用默认规则
        return calculate_token_score_default(total_m)
        
    except Exception as e:
        print(f"从数据库读取评分规则失败: {e}")
        # 失败时使用默认规则
        return calculate_token_score_default(total_m)

def calculate_token_score_default(total_m):
    """默认评分规则（当数据库规则不可用时）"""
    if total_m > 10:
        return -10, '日消耗 > 10M tokens'
    elif total_m >= 8:
        return -5, '日消耗 8-10M tokens'
    elif total_m >= 5:
        return -3, '日消耗 5-8M tokens'
    elif total_m >= 3:
        return -1, '日消耗 3-5M tokens'
    elif total_m >= 1:
        return 0, '日消耗 1-3M tokens'
    elif total_m >= 0.8:
        return 1, '日消耗 0.8-1M tokens'
    elif total_m >= 0.1:
        return 3, '日消耗 0.1-0.8M tokens'
    elif total_m >= 0.01:
        return 5, '日消耗 0.01-0.1M tokens'
    else:
        return 10, '日消耗 < 0.01M tokens'

@app.route('/api/tokens/update', methods=['POST'])
def api_tokens_update():
    """更新Token消耗数据"""
    try:
        data = request.json
        
        # 验证必要字段
        required_fields = ['date', 'total_tokens']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'status': 'error',
                    'message': f'缺少必要字段: {field}'
                }), 400
        
        total_tokens = data['total_tokens']
        
        # 获取分类数据
        dev_tokens = data.get('development_tokens', 0)
        search_tokens = data.get('search_tokens', 0)
        comm_tokens = data.get('communication_tokens', 0)
        other_tokens = data.get('other_tokens', 0)
        
        # 验证分类数据总和
        if total_tokens > 0 and (dev_tokens + search_tokens + comm_tokens + other_tokens) != total_tokens:
            return jsonify({
                'status': 'error',
                'message': f'分类数据总和({dev_tokens + search_tokens + comm_tokens + other_tokens})不等于总tokens({total_tokens})'
            }), 400
        
        # 计算分数
        total_m = total_tokens / 1000000
        score, reason = calculate_token_score(total_m)
        
        # 连接数据库
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 检查记录是否存在
        cursor.execute('SELECT date FROM token_daily_consumption WHERE date = ?', (data['date'],))
        if not cursor.fetchone():
            return jsonify({
                'status': 'error',
                'message': f'找不到日期为 {data["date"]} 的记录'
            }), 404
        
        # 更新token_daily_consumption表
        cursor.execute('''
            UPDATE token_daily_consumption 
            SET total_tokens = ?,
                development_tokens = ?,
                search_tokens = ?,
                communication_tokens = ?,
                other_tokens = ?,
                notes = ?
            WHERE date = ?
        ''', (
            total_tokens,
            dev_tokens,
            search_tokens,
            comm_tokens,
            other_tokens,
            data.get('notes', ''),
            data['date']
        ))
        
        # 更新daily_scores表
        cursor.execute('''
            INSERT OR REPLACE INTO daily_scores 
            (date, token_consumption, score, reason)
            VALUES (?, ?, ?, ?)
        ''', (
            data['date'],
            total_tokens,
            score,
            reason
        ))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': 'Token消耗数据更新成功',
            'data': {
                'date': data['date'],
                'total_tokens': total_tokens,
                'development_tokens': dev_tokens,
                'search_tokens': search_tokens,
                'communication_tokens': comm_tokens,
                'other_tokens': other_tokens,
                'score': score,
                'reason': reason,
                'updated_at': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'更新失败: {str(e)}'
        }), 500

@app.route('/api/tokens/delete', methods=['POST'])
def api_tokens_delete():
    """删除Token消耗数据"""
    try:
        data = request.json
        
        # 验证必要字段
        if 'date' not in data:
            return jsonify({
                'status': 'error',
                'message': '缺少必要字段: date'
            }), 400
        
        date = data['date']
        
        # 连接数据库
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 检查记录是否存在
        cursor.execute('SELECT date FROM token_daily_consumption WHERE date = ?', (date,))
        if not cursor.fetchone():
            return jsonify({
                'status': 'error',
                'message': f'找不到日期为 {date} 的记录'
            }), 404
        
        # 删除token_daily_consumption表记录
        cursor.execute('DELETE FROM token_daily_consumption WHERE date = ?', (date,))
        
        # 删除daily_scores表对应记录
        cursor.execute('DELETE FROM daily_scores WHERE date = ?', (date,))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': f'日期 {date} 的Token消耗数据已删除',
            'data': {
                'date': date,
                'deleted_at': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'删除失败: {str(e)}'
        }), 500

@app.route('/api/tokens/history')
def api_tokens_history():
    """获取Token消耗历史记录"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 检查表是否存在
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='token_daily_consumption'")
        if not cursor.fetchone():
            return jsonify({
                'status': 'success',
                'data': []
            })
        
        # 查询历史记录，按日期倒序
        cursor.execute('''
            SELECT 
                t.date,
                t.total_tokens,
                t.development_tokens,
                t.search_tokens,
                t.communication_tokens,
                t.other_tokens,
                t.notes,
                COALESCE(s.score, 0) as score,
                COALESCE(s.reason, '') as reason
            FROM token_daily_consumption t
            LEFT JOIN daily_scores s ON t.date = s.date
            ORDER BY t.date DESC
            LIMIT 50
        ''')
        
        rows = cursor.fetchall()
        records = []
        
        for row in rows:
            records.append({
                'date': row[0],
                'total_tokens': row[1],
                'development_tokens': row[2],
                'search_tokens': row[3],
                'communication_tokens': row[4],
                'other_tokens': row[5],
                'notes': row[6],
                'score': row[7],
                'reason': row[8]
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'data': records
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'查询失败: {str(e)}'
        }), 500

# ==================== 评分规则管理API ====================

@app.route('/api/scoring-rules', methods=['GET'])
def api_scoring_rules():
    """获取所有评分规则"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 获取所有规则，包括显示顺序
        cursor.execute('''
            SELECT 
                sr.id,
                sr.rule_name,
                sr.min_value,
                sr.max_value,
                sr.score,
                sr.description,
                sr.is_active,
                sr.created_at,
                sr.updated_at,
                sro.display_order
            FROM scoring_rules sr
            LEFT JOIN scoring_rule_order sro ON sr.id = sro.rule_id
            ORDER BY sro.display_order ASC
        ''')
        
        rows = cursor.fetchall()
        conn.close()
        
        rules = []
        for row in rows:
            rules.append({
                'id': row[0],
                'rule_name': row[1],
                'min_value': row[2],
                'max_value': row[3],
                'score': row[4],
                'description': row[5],
                'is_active': bool(row[6]),
                'created_at': row[7],
                'updated_at': row[8],
                'display_order': row[9]
            })
        
        return jsonify({
            'status': 'success',
            'data': rules
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'获取评分规则失败: {str(e)}'
        }), 500

@app.route('/api/scoring-rules', methods=['POST'])
def api_scoring_rules_update():
    """更新评分规则（批量）"""
    try:
        data = request.json
        
        if not data or 'rules' not in data:
            return jsonify({
                'status': 'error',
                'message': '缺少rules数据'
            }), 400
        
        rules = data['rules']
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 开始事务
        cursor.execute('BEGIN TRANSACTION')
        
        try:
            # 清空现有规则
            cursor.execute('DELETE FROM scoring_rules')
            cursor.execute('DELETE FROM scoring_rule_order')
            
            # 插入新规则
            for i, rule in enumerate(rules):
                cursor.execute('''
                    INSERT INTO scoring_rules 
                    (rule_name, min_value, max_value, score, description, is_active)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    rule.get('rule_name', '未分类'),
                    rule.get('min_value'),
                    rule.get('max_value'),
                    rule.get('score', 0),
                    rule.get('description', ''),
                    rule.get('is_active', True)
                ))
                
                rule_id = cursor.lastrowid
                cursor.execute('''
                    INSERT INTO scoring_rule_order (rule_id, display_order)
                    VALUES (?, ?)
                ''', (rule_id, i))
            
            # 提交事务
            conn.commit()
            
            return jsonify({
                'status': 'success',
                'message': '评分规则更新成功',
                'count': len(rules)
            })
            
        except Exception as e:
            # 回滚事务
            conn.rollback()
            raise e
            
        finally:
            conn.close()
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'更新评分规则失败: {str(e)}'
        }), 500

@app.route('/api/scoring-rules/test', methods=['POST'])
def api_scoring_rules_test():
    """测试评分规则"""
    try:
        data = request.json
        
        if not data or 'total_m' not in data:
            return jsonify({
                'status': 'error',
                'message': '缺少total_m参数'
            }), 400
        
        total_m = float(data['total_m'])
        
        # 使用当前规则计算分数
        score, reason = calculate_token_score(total_m)
        
        return jsonify({
            'status': 'success',
            'data': {
                'total_m': total_m,
                'score': score,
                'reason': reason,
                'total_tokens': int(total_m * 1000000)
            }
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'测试评分规则失败: {str(e)}'
        }), 500

@app.route('/api/scoring-rules/apply-to-history', methods=['POST'])
def api_scoring_rules_apply_to_history():
    """将新规则应用到历史数据"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 获取所有历史记录
        cursor.execute('SELECT date, total_tokens FROM token_daily_consumption')
        records = cursor.fetchall()
        
        updated_count = 0
        
        for date, total_tokens in records:
            if total_tokens > 0:
                total_m = total_tokens / 1000000
                score, reason = calculate_token_score(total_m)
                
                # 更新分数记录
                cursor.execute('''
                    INSERT OR REPLACE INTO daily_scores (date, token_consumption, score, reason)
                    VALUES (?, ?, ?, ?)
                ''', (date, total_tokens, score, reason))
                
                updated_count += 1
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'status': 'success',
            'message': f'已更新{updated_count}条历史记录的分数',
            'updated_count': updated_count
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'应用规则到历史数据失败: {str(e)}'
        }), 500

@app.route('/scoring-rules-editor')
def scoring_rules_editor_page():
    """评分规则编辑器页面"""
    return render_template('scoring_rules_editor.html')

@app.route('/data-stats-simple')
def data_stats_simple_page():
    """简化版数据统计页面"""
    return render_template('data_stats_simple.html')

@app.route('/data-stats-minimal')
def data_stats_minimal_page():
    """极简版数据统计页面"""
    return render_template('data_stats_minimal.html')

@app.route('/daily-test')
def daily_test_page():
    """日报测试页面"""
    return render_template('daily_test.html')

@app.route('/stocks-watchlist')
def stocks_watchlist():
    """股票关注列表页面"""
    return render_template('stocks_watchlist.html')

@app.route('/api/tokens/stats')
def get_token_stats_api():
    """获取Token统计信息API"""
    try:
        period = request.args.get('period', '7d')
        stats = get_token_stats(period)
        
        return jsonify({
            'success': True,
            'stats': stats,
            'period': period
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/tokens/charts')
def get_token_charts_api():
    """获取Token图表数据API"""
    try:
        period = request.args.get('period', '7d')
        trend_data = get_token_trend_data(period)
        
        # 分类数据
        stats = get_token_stats(period)
        category_data = {
            'categories': [cat['name'] for cat in stats['categories']],
            'percentages': [cat['percentage'] for cat in stats['categories']],
            'totals': [cat['total_tokens'] for cat in stats['categories']]
        }
        
        return jsonify({
            'success': True,
            'trend_data': trend_data,
            'category_data': category_data,
            'period': period
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/tokens/records')
def get_token_records_api():
    """获取Token消耗记录API"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        category = request.args.get('category')
        
        records_data = get_token_records(page, per_page, start_date, end_date, category)
        
        return jsonify({
            'success': True,
            'data': records_data
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/tokens/analysis')
def get_token_analysis_api():
    """获取Token分析建议API"""
    try:
        period = request.args.get('period', '7d')
        stats = get_token_stats(period)
        tips = get_optimization_tips(stats)
        budget_status = get_budget_status()
        
        return jsonify({
            'success': True,
            'tips': tips,
            'budget_status': budget_status,
            'stats': stats
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# ==================== 网络设备相关路由 ====================

@app.route('/network')
def network_devices():
    """网络设备页面"""
    scanner = NetworkScanner()
    
    # 获取所有设备
    devices = scanner.get_all_devices(limit=100)
    
    # 获取统计信息
    stats = scanner.get_device_stats()
    
    # 获取最近扫描时间
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT MAX(last_seen) as last_scan FROM network_devices')
    last_scan = cursor.fetchone()['last_scan']
    conn.close()
    
    return render_template('network.html',
                         devices=devices,
                         stats=stats,
                         last_scan=last_scan,
                         total_devices=len(devices),
                         now=datetime.now())

@app.route('/network/schedule')
def network_schedule():
    """网络扫描定时设置页面"""
    return render_template('network_schedule_simple_final.html')

@app.route('/api/network/devices')
def get_network_devices_api():
    """获取网络设备API"""
    scanner = NetworkScanner()
    
    # 支持分页和筛选
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)
    vendor = request.args.get('vendor')
    check_online = request.args.get('check_online', 'false').lower() == 'true'
    
    offset = (page - 1) * per_page
    devices = scanner.get_all_devices(limit=per_page, offset=offset)
    
    # 实时检查在线状态（如果请求）
    if check_online:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        import time
        
        def check_device_status(device):
            ip = device['ip_address']
            is_online = scanner.ping_host(ip)
            device['is_online'] = is_online
            device['last_online_check'] = datetime.now().isoformat()
            return device
        
        # 使用线程池并发检查
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(check_device_status, device) for device in devices]
            devices = [future.result() for future in as_completed(futures)]
    
    # 如果有厂商筛选
    if vendor and vendor != 'all':
        devices = [d for d in devices if d.get('vendor') == vendor]
    
    return jsonify({
        'success': True,
        'data': devices,
        'page': page,
        'per_page': per_page,
        'total': len(devices)
    })

# 全局变量存储扫描状态
scan_status = {
    'is_scanning': False,
    'progress': 0,
    'scanned': 0,
    'total': 254,
    'online': 0,
    'current_ip': '',
    'log': [],
    'start_time': None,
    'end_time': None
}

@app.route('/api/network/scan', methods=['POST'])
def scan_network_api():
    """扫描网络API（优化版）"""
    global scan_status
    
    if scan_status['is_scanning']:
        return jsonify({
            'success': False,
            'error': '扫描正在进行中，请稍后'
        }), 400
    
    try:
        # 获取扫描类型
        data = request.get_json() or {}
        scan_type = data.get('type', 'full')  # 'quick' or 'full'
        
        # 重置扫描状态
        scan_status = {
            'is_scanning': True,
            'scan_type': scan_type,
            'progress': 0,
            'scanned': 0,
            'total': 254 if scan_type == 'full' else 0,  # 快速扫描开始时不知道总数
            'online': 0,
            'current_ip': '',
            'log': [],
            'start_time': datetime.now().isoformat(),
            'start_timestamp': datetime.now().isoformat(),
            'end_time': None,
            'last_update_time': None,
            'last_scanned': 0,
            'scan_speed': 0,
            'avg_scan_speed': 0,
            'remaining_time': 30,
            'estimated_total_time': 30,
            'remaining_time_estimate': 30,
            'progress_percent': 0,
            'message': f'正在启动{ "快速" if scan_type == "quick" else "完整" }扫描...'
        }
        
        # 定义进度回调函数
        def progress_callback(data):
            global scan_status
            import time
            
            scan_status['progress'] = data['progress']
            scan_status['scanned'] = data['scanned']
            scan_status['online'] = data['online']
            scan_status['current_ip'] = data['current_ip']
            scan_status['last_update_time'] = time.time()
            
            # 更新总数（快速扫描）
            if scan_type == 'quick' and data['total'] > 0:
                scan_status['total'] = data['total']
            
            # 添加详细日志
            timestamp = datetime.now().strftime('%H:%M:%S')
            if data.get('error'):
                log_entry = f"[{timestamp}] ❌ {data['current_ip']}: {data['error']}"
                scan_status['current_status'] = 'error'
            elif data['is_online']:
                log_entry = f"[{timestamp}] ✅ {data['current_ip']}: 在线"
                scan_status['current_status'] = 'online'
            else:
                log_entry = f"[{timestamp}] ⚪ {data['current_ip']}: 离线"
                scan_status['current_status'] = 'offline'
            
            scan_status['log'].append(log_entry)
            # 只保留最近50条日志
            if len(scan_status['log']) > 50:
                scan_status['log'] = scan_status['log'][-50:]
            
            # 更新消息
            scan_status['message'] = f"{'快速' if scan_type == 'quick' else '完整'}扫描: {data['scanned']}/{data['total']} {'设备' if scan_type == 'quick' else 'IP'} ({data['online']} 在线)"
        
        # 运行扫描
        scanner = NetworkScanner()
        
        if scan_type == 'quick':
            # 快速扫描已知设备
            from concurrent.futures import ThreadPoolExecutor, as_completed
            
            # 获取已知设备
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM network_devices')
            known_devices = [dict(row) for row in cursor.fetchall()]
            conn.close()
            
            if not known_devices:
                scan_status['is_scanning'] = False
                return jsonify({
                    'success': True,
                    'message': '没有已知设备需要扫描',
                    'data': {'online': 0, 'total': 0}
                })
            
            # 并发检查
            results = []
            online_count = 0
            
            with ThreadPoolExecutor(max_workers=20) as executor:
                futures = {}
                
                for device in known_devices:
                    ip = device['ip_address']
                    future = executor.submit(scanner.ping_host, ip)
                    futures[future] = device
                
                # 处理结果
                for i, future in enumerate(as_completed(futures)):
                    device = futures[future]
                    is_online = future.result()
                    
                    # 更新设备状态
                    device['is_online'] = is_online
                    if is_online:
                        online_count += 1
                        device['last_online'] = datetime.now().isoformat()
                    
                    results.append(device)
                    
                    # 回调进度
                    progress_callback({
                        'current_ip': device['ip_address'],
                        'is_online': is_online,
                        'progress': (i + 1) / len(known_devices) * 100,
                        'scanned': i + 1,
                        'total': len(known_devices),
                        'online': online_count
                    })
            
            # 保存结果 - 确保所有设备都有'online'键
            for device in results:
                if 'is_online' in device and 'online' not in device:
                    device['online'] = device['is_online']
            
            # 快速扫描后，从ARP表发现新设备
            arp_cache = scanner.get_arp_cache()
            known_ips = {d['ip_address'] for d in known_devices}
            new_from_arp = 0
            for ip, mac in arp_cache.items():
                if ip.startswith('192.168.1.') and ip not in known_ips:
                    # 新设备，加入结果列表
                    results.append({
                        'ip_address': ip,
                        'mac_address': mac,
                        'online': True,
                        'is_online': True,
                        'hostname': None,
                        'vendor': scanner.get_vendor_from_mac(mac),
                        'response_time': None,
                        'scan_time': datetime.now().isoformat(),
                        'last_online': datetime.now().isoformat()
                    })
                    new_from_arp += 1
                    progress_callback({
                        'current_ip': ip,
                        'is_online': True,
                        'progress': 100,
                        'scanned': len(known_devices),
                        'total': len(known_devices),
                        'online': online_count + new_from_arp
                    })
            
            saved_count = scanner.save_to_database(results)
            devices = results
            
            if new_from_arp > 0:
                print(f"🆕 快速扫描从ARP表发现 {new_from_arp} 个新设备")
            
        else:
            # 完整扫描
            online_count, devices = scanner.scan_network(progress_callback=progress_callback)
            saved_count = scanner.save_to_database(devices)
        
        # 更新扫描状态
        scan_status['is_scanning'] = False
        scan_status['progress'] = 100
        scan_status['end_time'] = datetime.now().isoformat()
        scan_status['message'] = f"{'快速' if scan_type == 'quick' else '完整'}扫描完成: {online_count}/{len(devices)} 在线"
        
        # 计算耗时
        start_time = datetime.fromisoformat(scan_status['start_time'])
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        return jsonify({
            'success': True,
            'message': f"{'快速' if scan_type == 'quick' else '完整'}扫描完成",
            'data': {
                'duration_seconds': round(duration, 2),
                'ip_scanned': len(devices),
                'online_devices': online_count,
                'saved_to_db': saved_count,
                'start_time': scan_status['start_time'],
                'end_time': scan_status['end_time'],
                'scan_type': scan_type
            }
        })
        
    except Exception as e:
        scan_status['is_scanning'] = False
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/network/scan/status', methods=['GET'])
def get_scan_status_api():
    """获取扫描状态API（增强版）"""
    # 计算扫描速度
    if scan_status['is_scanning'] and scan_status.get('last_update_time'):
        import time
        current_time = time.time()
        last_time = scan_status['last_update_time']
        time_diff = current_time - last_time
        
        if time_diff > 0:
            scanned_diff = scan_status['scanned'] - scan_status.get('last_scanned', 0)
            scan_speed = scanned_diff / time_diff if time_diff > 0 else 0
            scan_status['scan_speed'] = round(scan_speed, 1)
        
        scan_status['last_update_time'] = current_time
        scan_status['last_scanned'] = scan_status['scanned']
    
    # 计算预计剩余时间（修复版）
    if scan_status['is_scanning'] and scan_status.get('start_time'):
        from datetime import datetime
        
        try:
            start_time = datetime.fromisoformat(scan_status['start_time'])
            elapsed = (datetime.now() - start_time).total_seconds()
            
            if scan_status['progress'] > 0:
                # 计算预计总时间
                total_estimated = elapsed * 100 / scan_status['progress']
                remaining = max(0, total_estimated - elapsed)
                
                # 更新状态
                scan_status['estimated_total_time'] = round(total_estimated, 1)
                scan_status['remaining_time'] = round(remaining, 1)
                
                # 计算预计完成时间
                estimated_end_time = start_time + timedelta(seconds=total_estimated)
                scan_status['estimated_end_time'] = estimated_end_time.isoformat()
                
                # 计算进度百分比
                scan_status['progress_percent'] = round(scan_status['progress'], 1)
                
                # 计算平均扫描速度
                if elapsed > 0:
                    avg_speed = scan_status['scanned'] / elapsed
                    scan_status['avg_scan_speed'] = round(avg_speed, 2)
                    
                    # 计算剩余IP数量
                    remaining_ips = scan_status['total'] - scan_status['scanned']
                    if avg_speed > 0:
                        remaining_time_estimate = remaining_ips / avg_speed
                        scan_status['remaining_time_estimate'] = round(remaining_time_estimate, 1)
            else:
                # 如果还没有进度，使用默认估计
                scan_status['remaining_time'] = 30  # 默认30秒
                scan_status['estimated_total_time'] = 30
                scan_status['remaining_time_estimate'] = 30
                
        except Exception as e:
            # 如果计算失败，设置默认值
            scan_status['remaining_time'] = 30
            scan_status['estimated_total_time'] = 30
            scan_status['remaining_time_estimate'] = 30
    else:
        # 扫描未进行或已完成
        scan_status['remaining_time'] = 0
        scan_status['estimated_total_time'] = 0
        scan_status['remaining_time_estimate'] = 0
    
    return jsonify({
        'success': True,
        'data': scan_status
    })

@app.route('/api/network/devices/<int:device_id>/notes', methods=['PUT'])
def update_device_notes_api(device_id):
    """更新设备备注API"""
    data = request.get_json()
    
    if 'notes' not in data:
        return jsonify({'success': False, 'error': '缺少notes字段'}), 400
    
    scanner = NetworkScanner()
    success = scanner.update_device_notes(device_id, data['notes'])
    
    if success:
        return jsonify({
            'success': True,
            'message': '备注更新成功'
        })
    else:
        return jsonify({
            'success': False,
            'error': '设备不存在或更新失败'
        }), 404

@app.route('/api/network/stats')
def get_network_stats_api():
    """获取网络统计API"""
    scanner = NetworkScanner()
    stats = scanner.get_device_stats()
    
    return jsonify({
        'success': True,
        'data': stats
    })

@app.route('/cron')
def cron_page():
    """定时任务管理页面"""
    return render_template('cron.html')

@app.route('/cron/openclaw')
def openclaw_cron_page():
    """OpenClaw定时任务管理页面"""
    # 获取任务数据
    try:
        cron_manager = get_cron_manager()
        tasks = cron_manager.get_all_tasks()
        
        # 调试：打印任务数据
        print(f"=== 调试: 获取到 {len(tasks)} 个任务 ===")
        for i, task in enumerate(tasks):
            print(f"任务 {i}: {task.get('name')}")
            print(f"  nextRun值: {repr(task.get('nextRun'))}")
            print(f"  nextRun类型: {type(task.get('nextRun'))}")
        
        # 预处理任务数据，确保所有字段都有值
        for task in tasks:
            original_nextRun = task.get('nextRun')
            task['nextRun'] = original_nextRun or ''
            task['token_estimate'] = task.get('token_estimate') or 0
            task['token_efficiency'] = task.get('token_efficiency') or 0
            task['lastRunStatus'] = task.get('lastRunStatus') or '未知'
            
        return render_template('openclaw_cron.html', tasks=tasks)
    except Exception as e:
        print(f"错误: {e}")
        # 如果出错，返回空任务列表
        return render_template('openclaw_cron.html', tasks=[])

@app.route('/test_cron')
def test_cron_page():
    """测试页面"""
    return render_template('test_cron.html')

@app.route('/test_simple')
def test_simple_page():
    """简单测试页面"""
    return render_template('test_simple.html')

# 旧的 /api/cron/tasks 路由已删除，使用新的优化版本

@app.route('/health')
def health_check():
    """健康检查端点"""
    return jsonify({
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'service': 'Pablo智能演进看板'
    })

@app.route('/api/monitor/status', methods=['GET'])
def monitor_status():
    """获取监控状态"""
    try:
        # 检查服务状态
        import subprocess
        import json
        
        # 检查Web服务
        web_result = subprocess.run(
            ['curl', '-s', 'http://localhost:5000/health'],
            capture_output=True,
            text=True,
            timeout=5
        )
        web_ok = web_result.returncode == 0 and '"status": "healthy"' in web_result.stdout
        
        # 检查数据库
        db_ok = False
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            db_ok = True
            conn.close()
        except:
            db_ok = False
        
        # 检查监控进程
        monitor_pid = None
        monitor_running = False
        try:
            with open('monitor.pid', 'r') as f:
                monitor_pid = int(f.read().strip())
                monitor_running = subprocess.run(['ps', '-p', str(monitor_pid)], 
                                               capture_output=True).returncode == 0
        except:
            pass
        
        return jsonify({
            'status': 'success',
            'monitoring': {
                'web_service': {
                    'status': 'healthy' if web_ok else 'unhealthy',
                    'checked_at': datetime.now().isoformat()
                },
                'database': {
                    'status': 'healthy' if db_ok else 'unhealthy',
                    'checked_at': datetime.now().isoformat()
                },
                'monitor_service': {
                    'running': monitor_running,
                    'pid': monitor_pid,
                    'checked_at': datetime.now().isoformat()
                }
            },
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/monitor/start', methods=['POST'])
def start_monitor():
    """启动监控服务"""
    try:
        import subprocess
        
        # 检查是否已运行
        monitor_running = False
        try:
            with open('monitor.pid', 'r') as f:
                pid = int(f.read().strip())
                monitor_running = subprocess.run(['ps', '-p', str(pid)], 
                                               capture_output=True).returncode == 0
        except:
            pass
        
        if monitor_running:
            return jsonify({
                'status': 'success',
                'message': '监控服务已在运行',
                'timestamp': datetime.now().isoformat()
            })
        
        # 启动监控服务
        result = subprocess.run(
            ['./start_monitor.sh'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return jsonify({
                'status': 'success',
                'message': '监控服务已启动',
                'output': result.stdout,
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'status': 'error',
                'message': '启动监控服务失败',
                'output': result.stderr,
                'timestamp': datetime.now().isoformat()
            }), 500
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/monitor/stop', methods=['POST'])
def stop_monitor():
    """停止监控服务"""
    try:
        import subprocess
        
        # 检查是否在运行
        monitor_running = False
        try:
            with open('monitor.pid', 'r') as f:
                pid = int(f.read().strip())
                monitor_running = subprocess.run(['ps', '-p', str(pid)], 
                                               capture_output=True).returncode == 0
        except:
            pass
        
        if not monitor_running:
            return jsonify({
                'status': 'success',
                'message': '监控服务未在运行',
                'timestamp': datetime.now().isoformat()
            })
        
        # 停止监控服务
        result = subprocess.run(
            ['./stop_monitor.sh'],
            capture_output=True,
            text=True,
            timeout=10
        )
        
        if result.returncode == 0:
            return jsonify({
                'status': 'success',
                'message': '监控服务已停止',
                'output': result.stdout,
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'status': 'error',
                'message': '停止监控服务失败',
                'output': result.stderr,
                'timestamp': datetime.now().isoformat()
            }), 500
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# ==================== Cron 任务管理 API ====================

@app.route('/api/cron/tasks', methods=['GET'])
def get_cron_tasks():
    """获取所有定时任务（集成token预估）"""
    try:
        # 导入token工具
        try:
            from task_token_utils import get_task_token_estimate, get_task_token_stats
        except ImportError:
            # 备用方案：直接定义函数
            def get_task_token_estimate(task_id):
                # 简单预估逻辑
                estimates = {
                    'network_scan_script': 0,
                    'service_monitor_script': 0,
                    'daily_review_script': 0,
                    'token_report_script': 0,
                    'weekly_summary_script': 0,
                    'daily_tech_news_script': 100,  # 混合任务
                    'daily_stock_news_script': 200   # AI分析较多
                }
                return {'total_estimate': estimates.get(task_id, 0)}
            
            def get_task_token_stats(task_id):
                return {
                    'total_actual_tokens': 0,
                    'avg_actual_tokens': 0,
                    'run_count': 0,
                    'today_tokens': 0
                }
        
        # 优化后的任务配置 - 使用脚本替代AI任务
        optimized_tasks = [
            {
                'id': 'network_scan_script',
                'name': '网络设备扫描（脚本）',
                'enabled': True,
                'schedule': {
                    'kind': 'cron',
                    'expr': '30 18 * * *',
                    'tz': 'Asia/Shanghai'
                },
                'payload': {
                    'kind': 'systemEvent',
                    'text': '执行网络扫描脚本: python scripts/network_scan.py'
                },
                'description': '每天18:30执行Python脚本扫描网络，更新设备状态',
                'nextRun': '2026-03-28T18:30:00+08:00',
                'lastRun': '2026-03-27T18:30:00+08:00',
                'execution_count': 3
            },
            {
                'id': 'service_monitor_script',
                'name': 'Pablo服务监控（脚本）',
                'enabled': True,
                'schedule': {
                    'kind': 'every',
                    'everyMs': 300000  # 5分钟
                },
                'payload': {
                    'kind': 'systemEvent',
                    'text': '执行服务监控脚本: python service_monitor.py'
                },
                'description': '每5分钟执行Python脚本检查服务健康状态',
                'nextRun': '2026-03-28T18:05:00+08:00',
                'lastRun': '2026-03-28T18:00:00+08:00',
                'token_estimate': 0,
                'avg_token': 0,
                'total_token': 0,
                'execution_count': 30
            },
            {
                'id': 'daily_review_script',
                'name': '每日回顾报告（脚本）',
                'enabled': True,
                'schedule': {
                    'kind': 'cron',
                    'expr': '0 20 * * *',
                    'tz': 'Asia/Shanghai'
                },
                'payload': {
                    'kind': 'systemEvent',
                    'text': '执行每日回顾脚本: python scripts/daily_review.py'
                },
                'description': '每日20:00执行Python脚本生成评分和改进报告',
                'nextRun': '2026-03-28T20:00:00+08:00',
                'lastRun': None,
                'token_estimate': 0,
                'avg_token': 0,
                'total_token': 0,
                'execution_count': 0
            },
            {
                'id': 'token_report_script',
                'name': 'Token消耗报告（脚本）',
                'enabled': True,
                'schedule': {
                    'kind': 'cron',
                    'expr': '0 23 * * *',
                    'tz': 'Asia/Shanghai'
                },
                'payload': {
                    'kind': 'systemEvent',
                    'text': '执行Token报告脚本: python scripts/token_report.py'
                },
                'description': '每日23:00执行Python脚本生成Token消耗报告',
                'nextRun': '2026-03-28T23:00:00+08:00',
                'lastRun': None,
                'token_estimate': 0,
                'avg_token': 0,
                'total_token': 0,
                'execution_count': 0
            },
            {
                'id': 'weekly_summary_script',
                'name': '周度总结报告（脚本）',
                'enabled': True,
                'schedule': {
                    'kind': 'cron',
                    'expr': '0 9 * * 1',
                    'tz': 'Asia/Shanghai'
                },
                'payload': {
                    'kind': 'systemEvent',
                    'text': '执行周度总结脚本: python scripts/weekly_summary.py'
                },
                'description': '每周一9:00执行Python脚本生成周度总结报告',
                'nextRun': '2026-03-31T09:00:00+08:00',
                'lastRun': '2026-03-24T09:00:00+08:00',
                'token_estimate': 0,
                'avg_token': 0,
                'total_token': 0,
                'execution_count': 3
            },
            {
                'id': 'daily_tech_news_script',
                'name': '每日科技新闻摘要（脚本）',
                'enabled': True,
                'schedule': {
                    'kind': 'cron',
                    'expr': '0 7 * * *',
                    'tz': 'Asia/Shanghai'
                },
                'payload': {
                    'kind': 'systemEvent',
                    'text': '执行科技新闻摘要脚本: python daily_news_summary.py'
                },
                'description': '每天7:00获取科技、硬件、AI、大模型、互联网、美团Top 10重要新闻，3-10句话摘要，单条≤100字',
                'nextRun': '2026-03-29T07:00:00+08:00',
                'lastRun': None,
                'token_estimate': 0,
                'avg_token': 0,
                'total_token': 0,
                'execution_count': 0
            },
            {
                'id': 'daily_stock_news_script',
                'name': '每日股票新闻分析（脚本）',
                'enabled': True,
                'schedule': {
                    'kind': 'cron',
                    'expr': '30 10 * * *',
                    'tz': 'Asia/Shanghai'
                },
                'payload': {
                    'kind': 'systemEvent',
                    'text': '执行股票新闻分析脚本: python stock_news_analysis.py'
                },
                'description': '每天10:30分析美团、阿里、腾讯、字节、中芯国际、兴森科技、铂科新材、中兴通讯、地平线、越疆的股票新闻，预测未来10天走势，3-5句话摘要，单条≤50字',
                'nextRun': '2026-03-29T10:30:00+08:00',
                'lastRun': None,
                'token_estimate': 0,
                'avg_token': 0,
                'total_token': 0,
                'execution_count': 0
            }
        ]
        
        return jsonify({
            'status': 'success',
            'tasks': optimized_tasks,
            'total': len(optimized_tasks),
            'timestamp': datetime.now().isoformat(),
            'optimization_note': '已优化：所有任务使用脚本执行，避免AI token消耗'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500
        
        return jsonify({
            'status': 'success',
            'tasks': mock_tasks,
            'total': len(mock_tasks),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500


@app.route('/api/cron/tasks-with-tokens', methods=['GET'])
def get_cron_tasks_with_tokens():
    """获取所有定时任务（包含真实token消耗）"""
    try:
        import sqlite3
        from datetime import datetime, timedelta
        
        db_path = 'scores.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        # 任务定义 - 所有任务已关闭
        tasks = [
            {
                'id': 'daily_news',
                'name': '每日7:00新闻摘要',
                'enabled': False,
                'description': '每天7:00获取新闻摘要（已关闭）',
                'is_ai_task': True,
                'schedule_time': '07:00'
            },
            {
                'id': 'daily_weather',
                'name': '每日7:00天气更新',
                'enabled': False,
                'description': '每天7:00获取廊坊天气（已关闭）',
                'is_ai_task': True,
                'schedule_time': '07:00'
            },
            {
                'id': 'daily_report_push',
                'name': '每日7:30日报推送',
                'enabled': False,
                'description': '每天7:30推送日报（已关闭）',
                'is_ai_task': False,
                'schedule_time': '07:30'
            },
            {
                'id': 'service_monitor',
                'name': 'Pablo服务监控',
                'enabled': False,
                'description': '每天22:00检查Pablo Web服务状态（已关闭）',
                'is_ai_task': True,
                'schedule_time': '22:00'
            },
            {
                'id': 'daily_generate',
                'name': '每日10:30日报生成',
                'enabled': False,
                'description': '每天10:30生成日报（已关闭）',
                'is_ai_task': True,
                'schedule_time': '10:30'
            },
            {
                'id': 'daily_improvement',
                'name': '每日改进回顾',
                'enabled': False,
                'description': '每天20:00改进回顾（已关闭）',
                'is_ai_task': True,
                'schedule_time': '20:00'
            },
            {
                'id': 'network_scan',
                'name': '每日18:30网络扫描',
                'enabled': False,
                'description': '每天18:30网络设备扫描（已关闭）',
                'is_ai_task': False,
                'schedule_time': '18:30'
            }
        ]
        
        # 为每个任务添加真实token数据
        for task in tasks:
            task_name = task['name']
            
            # 1. 从真实数据表查询
            cursor.execute('SELECT SUM(tokens) as total_tokens, COUNT(*) as run_count, MAX(execution_time) as last_time FROM cron_real_tokens WHERE job_name = ? AND execution_date = ?', (task_name, today))
            
            today_row = cursor.fetchone()
            if today_row:
                today_tokens, today_runs, last_time = today_row
                today_tokens = today_tokens or 0
                today_runs = today_runs or 0
            else:
                today_tokens = today_runs = 0
                last_time = None
            
            # 2. 昨日数据
            cursor.execute('SELECT SUM(tokens), COUNT(*) FROM cron_real_tokens WHERE job_name = ? AND execution_date = ?', (task_name, yesterday))
            
            yesterday_row = cursor.fetchone()
            if yesterday_row:
                yesterday_tokens, yesterday_runs = yesterday_row
                yesterday_tokens = yesterday_tokens or 0
                yesterday_runs = yesterday_runs or 0
            else:
                yesterday_tokens = yesterday_runs = 0
            
            # 3. 预估消耗
            if task['is_ai_task']:
                if '新闻' in task_name:
                    estimate = 5800
                elif '天气' in task_name:
                    estimate = 2200
                elif '监控' in task_name:
                    estimate = 800
                elif '日报' in task_name:
                    estimate = 2800
                elif '改进' in task_name:
                    estimate = 1300
                else:
                    estimate = 2000
            else:
                estimate = 0
            
            # 添加到任务
            task['token_estimate'] = estimate
            task['token_actual_total'] = today_tokens
            task['token_actual_today'] = today_tokens
            task['token_actual_yesterday'] = yesterday_tokens
            task['run_count_today'] = today_runs
            task['run_count_yesterday'] = yesterday_runs
            task['last_execution'] = last_time
            
            # 效率计算
            if today_runs > 0 and estimate > 0:
                avg_per_run = today_tokens / today_runs
                efficiency = (avg_per_run / estimate * 100) if estimate > 0 else 100
                task['token_efficiency'] = round(efficiency, 1)
            else:
                task['token_efficiency'] = 0 if estimate == 0 else 100
        
        conn.close()
        
        # 统计信息
        total_tasks = len(tasks)
        active_tasks = len([t for t in tasks if t['enabled']])
        ai_tasks = len([t for t in tasks if t['enabled'] and t['is_ai_task']])
        script_tasks = len([t for t in tasks if t['enabled'] and not t['is_ai_task']])
        
        total_token_today = sum(t['token_actual_today'] for t in tasks)
        total_token_yesterday = sum(t['token_actual_yesterday'] for t in tasks)
        total_runs_today = sum(t['run_count_today'] for t in tasks)
        
        return jsonify({
            'status': 'success',
            'real_data': True,
            'tasks': tasks,
            'stats': {
                'total_tasks': total_tasks,
                'active_tasks': active_tasks,
                'ai_tasks': ai_tasks,
                'script_tasks': script_tasks,
                'total_token_today': total_token_today,
                'total_token_yesterday': total_token_yesterday,
                'total_runs_today': total_runs_today,
                'date': today,
                'yesterday': yesterday
            }
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'获取任务数据失败: {str(e)}'
        }), 500@app.route('/api/cron/tasks', methods=['POST'])
def create_cron_task():
    """创建新的定时任务"""
    try:
        data = request.get_json()
        
        # 验证必要字段
        required_fields = ['name', 'schedule', 'payload']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'status': 'error',
                    'message': f'缺少必要字段: {field}',
                    'timestamp': datetime.now().isoformat()
                }), 400
        
        # 在实际应用中，这里会调用OpenClaw创建任务
        # 暂时模拟创建
        
        new_task = {
            'id': f'task_{int(datetime.now().timestamp())}',
            'name': data['name'],
            'enabled': data.get('enabled', True),
            'schedule': data['schedule'],
            'payload': data['payload'],
            'description': data.get('description', ''),
            'nextRun': datetime.now().isoformat(),
            'lastRun': None,
            'token_estimate': data.get('token_estimate', 0),
            'avg_token': 0,
            'total_token': 0,
            'execution_count': 0
        }
        
        return jsonify({
            'status': 'success',
            'message': '任务创建成功',
            'task': new_task,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/cron/tasks/<task_id>', methods=['PUT'])
def update_cron_task(task_id):
    """更新定时任务"""
    try:
        data = request.get_json()
        
        # 模拟更新
        # 在实际应用中，这里会调用OpenClaw更新任务
        
        return jsonify({
            'status': 'success',
            'message': f'任务 {task_id} 更新成功',
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/cron/tasks/<task_id>', methods=['DELETE'])
def delete_cron_task(task_id):
    """删除定时任务"""
    try:
        # 模拟删除
        # 在实际应用中，这里会调用OpenClaw删除任务
        
        return jsonify({
            'status': 'success',
            'message': f'任务 {task_id} 删除成功',
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/cron/tasks/<task_id>/toggle', methods=['POST'])
def toggle_cron_task(task_id):
    """启用/禁用定时任务"""
    try:
        data = request.get_json()
        enabled = data.get('enabled', True)
        
        # 模拟切换状态
        # 在实际应用中，这里会调用OpenClaw更新任务状态
        
        return jsonify({
            'status': 'success',
            'message': f'任务 {task_id} 已{"启用" if enabled else "禁用"}',
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# ==================== OpenClaw定时任务API ====================

@app.route('/api/openclaw/cron/tasks', methods=['GET'])
def get_openclaw_cron_tasks():
    """获取OpenClaw所有定时任务"""
    try:
        cron_manager = get_cron_manager()
        tasks = cron_manager.get_all_tasks()
        
        return jsonify({
            'status': 'success',
            'tasks': tasks,
            'total': len(tasks),
            'enabled_count': len([t for t in tasks if t['enabled']]),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'获取OpenClaw定时任务失败: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/openclaw/cron/tasks/<task_id>/enabled', methods=['PUT'])
def update_openclaw_task_enabled(task_id):
    """更新OpenClaw任务启用状态"""
    try:
        data = request.get_json()
        
        if 'enabled' not in data:
            return jsonify({
                'status': 'error',
                'message': '缺少enabled字段',
                'timestamp': datetime.now().isoformat()
            }), 400
        
        cron_manager = get_cron_manager()
        success = cron_manager.update_task_enabled(task_id, data['enabled'])
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'任务 {task_id} 状态更新成功',
                'enabled': data['enabled'],
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'任务 {task_id} 未找到',
                'timestamp': datetime.now().isoformat()
            }), 404
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'更新任务状态失败: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/openclaw/cron/tasks/<task_id>/schedule', methods=['PUT'])
def update_openclaw_task_schedule(task_id):
    """更新OpenClaw任务调度时间"""
    try:
        data = request.get_json()
        
        required_fields = ['schedule_expr']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'status': 'error',
                    'message': f'缺少必要字段: {field}',
                    'timestamp': datetime.now().isoformat()
                }), 400
        
        cron_manager = get_cron_manager()
        schedule_tz = data.get('schedule_tz', 'Asia/Shanghai')
        success = cron_manager.update_task_schedule(task_id, data['schedule_expr'], schedule_tz)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'任务 {task_id} 调度时间更新成功',
                'schedule_expr': data['schedule_expr'],
                'schedule_tz': schedule_tz,
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'任务 {task_id} 未找到',
                'timestamp': datetime.now().isoformat()
            }), 404
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'更新任务调度失败: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/openclaw/cron/tasks', methods=['POST'])
def create_openclaw_cron_task():
    """创建新的OpenClaw定时任务"""
    try:
        data = request.get_json()
        
        required_fields = ['name', 'schedule_expr', 'payload_text']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'status': 'error',
                    'message': f'缺少必要字段: {field}',
                    'timestamp': datetime.now().isoformat()
                }), 400
        
        cron_manager = get_cron_manager()
        task_id = cron_manager.add_task(data)
        
        return jsonify({
            'status': 'success',
            'message': '任务创建成功',
            'task_id': task_id,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'创建任务失败: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/openclaw/cron/tasks/<task_id>', methods=['DELETE'])
def delete_openclaw_cron_task(task_id):
    """删除OpenClaw定时任务"""
    try:
        cron_manager = get_cron_manager()
        success = cron_manager.delete_task(task_id)
        
        if success:
            return jsonify({
                'status': 'success',
                'message': f'任务 {task_id} 删除成功',
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'status': 'error',
                'message': f'任务 {task_id} 未找到',
                'timestamp': datetime.now().isoformat()
            }), 404
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'删除任务失败: {str(e)}',
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/cron/tasks/<task_id>/run', methods=['POST'])
def run_cron_task(task_id):
    """立即执行定时任务"""
    try:
        # 模拟立即执行
        # 在实际应用中，这里会调用OpenClaw触发任务执行
        
        return jsonify({
            'status': 'success',
            'message': f'任务 {task_id} 已加入执行队列',
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# ==================== Token 消耗追踪 API ====================

@app.route('/api/tokens/record', methods=['POST'])
def record_token_usage():
    """记录token使用"""
    try:
        data = request.get_json()
        
        # 验证必要字段
        required_fields = ['input_tokens', 'output_tokens', 'category']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'status': 'error',
                    'message': f'缺少必要字段: {field}',
                    'timestamp': datetime.now().isoformat()
                }), 400
        
        # 导入tracker
        from token_tracker import TokenTracker
        tracker = TokenTracker()
        
        date_str = data.get('date', datetime.now().strftime('%Y-%m-%d'))
        
        success = tracker.record_usage(
            date_str=date_str,
            input_tokens=int(data['input_tokens']),
            output_tokens=int(data['output_tokens']),
            category=data['category'],
            task_name=data.get('task_name', ''),
            description=data.get('description', ''),
            session_id=data.get('session_id', '')
        )
        
        if success:
            return jsonify({
                'status': 'success',
                'message': 'Token使用记录已保存',
                'timestamp': datetime.now().isoformat()
            })
        else:
            return jsonify({
                'status': 'error',
                'message': '保存Token记录失败',
                'timestamp': datetime.now().isoformat()
            }), 500
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/tokens/daily/<date_str>', methods=['GET'])
def get_daily_token_summary(date_str):
    """获取每日token汇总"""
    try:
        from token_tracker import TokenTracker
        tracker = TokenTracker()
        
        summary = tracker.get_daily_summary(date_str)
        
        return jsonify({
            'status': 'success',
            'summary': summary,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/tokens/record-daily', methods=['POST'])
def record_daily_token_consumption():
    """记录每日总Token消耗（按分类）"""
    try:
        data = request.get_json()
        
        # 验证必要字段
        required_fields = ['total_tokens', 'date']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'缺少必要字段: {field}',
                    'timestamp': datetime.now().isoformat()
                }), 400
        
        # 获取各分类Token数（默认为0）
        development_tokens = data.get('development_tokens', 0)
        search_tokens = data.get('search_tokens', 0)
        communication_tokens = data.get('communication_tokens', 0)
        other_tokens = data.get('other_tokens', 0)
        
        # 验证总和（可选，前端已经验证）
        total_calculated = development_tokens + search_tokens + communication_tokens + other_tokens
        total_from_request = data['total_tokens']
        
        if total_calculated != total_from_request:
            return jsonify({
                'success': False,
                'error': f'各分类Token数之和 ({total_calculated}) 不等于总Token数 ({total_from_request})',
                'timestamp': datetime.now().isoformat()
            }), 400
        
        # 连接到数据库
        conn = sqlite3.connect('scores.db')
        cursor = conn.cursor()
        
        # 检查是否已存在该日期的记录
        cursor.execute('''
            SELECT date FROM token_daily_consumption WHERE date = ?
        ''', (data['date'],))
        
        existing = cursor.fetchone()
        
        if existing:
            # 更新现有记录
            cursor.execute('''
                UPDATE token_daily_consumption 
                SET total_tokens = ?,
                    development_tokens = ?,
                    search_tokens = ?,
                    communication_tokens = ?,
                    other_tokens = ?,
                    notes = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE date = ?
            ''', (
                total_from_request,
                development_tokens,
                search_tokens,
                communication_tokens,
                other_tokens,
                data.get('notes', ''),
                data['date']
            ))
            action = 'updated'
        else:
            # 插入新记录
            cursor.execute('''
                INSERT INTO token_daily_consumption 
                (date, total_tokens, development_tokens, search_tokens, 
                 communication_tokens, other_tokens, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            ''', (
                data['date'],
                total_from_request,
                development_tokens,
                search_tokens,
                communication_tokens,
                other_tokens,
                data.get('notes', '')
            ))
            action = 'created'
        
        # 同时保存到token_usage表（为了在"最近记录"中显示）
        # 将总token数按输入/输出分配（这里简单地将80%作为输入，20%作为输出）
        input_tokens = int(total_from_request * 0.8)
        output_tokens = total_from_request - input_tokens
        
        cursor.execute('''
            INSERT INTO token_usage 
            (date, task_name, input_tokens, output_tokens, total_tokens, category, description)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            data['date'],
            '总Token消耗录入',
            input_tokens,
            output_tokens,
            total_from_request,
            'other',  # 默认分类为other
            data.get('notes', '快速录入总Token消耗') or '快速录入总Token消耗'
        ))
        
        conn.commit()
        conn.close()
        
        return jsonify({
            'success': True,
            'message': f'Token消耗记录{action}成功',
            'date': data['date'],
            'total_tokens': total_from_request,
            'distribution': {
                'development': development_tokens,
                'search': search_tokens,
                'communication': communication_tokens,
                'other': other_tokens
            },
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/tokens/distribution', methods=['GET'])
def get_token_distribution():
    """获取历史Token分配比例"""
    try:
        # 连接到数据库
        conn = sqlite3.connect('scores.db')
        cursor = conn.cursor()
        
        # 获取最近30天的数据来计算比例
        cursor.execute('''
            SELECT 
                SUM(development_tokens) as dev_total,
                SUM(search_tokens) as search_total,
                SUM(communication_tokens) as comm_total,
                SUM(other_tokens) as other_total,
                SUM(total_tokens) as grand_total
            FROM token_daily_consumption 
            WHERE date >= date('now', '-30 days')
        ''')
        
        result = cursor.fetchone()
        conn.close()
        
        dev_total = result[0] or 0
        search_total = result[1] or 0
        comm_total = result[2] or 0
        other_total = result[3] or 0
        grand_total = result[4] or 0
        
        # 计算比例
        if grand_total > 0:
            distribution = {
                'development': dev_total / grand_total,
                'search': search_total / grand_total,
                'communication': comm_total / grand_total,
                'other': other_total / grand_total
            }
        else:
            # 如果没有历史数据，使用默认比例
            distribution = {
                'development': 0.4,   # 40%
                'search': 0.3,        # 30%
                'communication': 0.2,  # 20%
                'other': 0.1          # 10%
            }
        
        return jsonify({
            'success': True,
            'distribution': distribution,
            'totals': {
                'development': dev_total,
                'search': search_total,
                'communication': comm_total,
                'other': other_total,
                'grand_total': grand_total
            },
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/tokens/today', methods=['GET'])
def get_today_token_summary():
    """获取token汇总 - 显示昨日数据作为参考"""
    try:
        today = datetime.now().strftime('%Y-%m-%d')
        yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 查询昨日数据（作为主要显示）
        cursor.execute('''
            SELECT total_tokens, development_tokens, search_tokens, 
                   communication_tokens, other_tokens, notes
            FROM token_daily_consumption 
            WHERE date = ?
        ''', (yesterday,))
        
        yesterday_row = cursor.fetchone()
        
        # 查询今日数据
        cursor.execute('SELECT total_tokens FROM token_daily_consumption WHERE date = ?', (today,))
        today_row = cursor.fetchone()
        
        conn.close()
        
        if yesterday_row:
            # 有昨日数据
            total_tokens = yesterday_row[0]
            dev_tokens = yesterday_row[1] if yesterday_row[1] is not None else int(total_tokens * 0.55)
            search_tokens = yesterday_row[2] if yesterday_row[2] is not None else int(total_tokens * 0.15)
            comm_tokens = yesterday_row[3] if yesterday_row[3] is not None else int(total_tokens * 0.20)
            other_tokens = yesterday_row[4] if yesterday_row[4] is not None else int(total_tokens * 0.10)
            notes = f'昨日数据({yesterday}): {total_tokens:,} tokens'
            
            # 计算相比前日的变化（需要前日数据）
            day_before = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d')
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute('SELECT total_tokens FROM token_daily_consumption WHERE date = ?', (day_before,))
            day_before_row = cursor.fetchone()
            conn.close()
            
            change_percent = 0.0
            if day_before_row and day_before_row[0] > 0:
                change = total_tokens - day_before_row[0]
                change_percent = round(change / day_before_row[0] * 100, 1)
        else:
            # 无昨日数据，显示今日数据或0
            if today_row:
                total_tokens = today_row[0]
                notes = '今日数据'
            else:
                total_tokens = 0
                notes = '暂无数据'
            
            dev_tokens = int(total_tokens * 0.55)
            search_tokens = int(total_tokens * 0.15)
            comm_tokens = int(total_tokens * 0.20)
            other_tokens = int(total_tokens * 0.10)
            change_percent = 0.0
        
        # 今日实际消耗（用于记录）
        today_tokens = today_row[0] if today_row else 0
        
        response_data = {
            'status': 'success',
            'timestamp': datetime.now().isoformat(),
            'display_date': yesterday if yesterday_row else today,
            'display_tokens': total_tokens,
            'display_total_m': round(total_tokens / 1000000, 2) if total_tokens > 0 else 0,
            'change_percent': change_percent,
            'dev_tokens': dev_tokens,
            'search_tokens': search_tokens,
            'comm_tokens': comm_tokens,
            'other_tokens': other_tokens,
            'notes': notes,
            'today_actual_tokens': today_tokens,
            'data_source': 'yesterday' if yesterday_row else 'today'
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/tokens/lifetime', methods=['GET'])
def get_lifetime_token_summary():
    """获取历史累计token汇总"""
    try:
        from token_tracker import TokenTracker
        tracker = TokenTracker()
        
        summary = tracker.get_lifetime_summary()
        
        # 转换为前端期望的格式
        lifetime_data = summary.get('lifetime', {}) if summary else {}
        data = {
            'total_tokens': lifetime_data.get('total_tokens', 0),
            'avg_daily': 0,  # 需要计算
            'peak_daily': 0,  # 需要计算
            'days_counted': lifetime_data.get('days_active', 0)
        }
        
        # 计算日均（如果有数据）
        if data['days_counted'] > 0:
            data['avg_daily'] = data['total_tokens'] / data['days_counted']
        
        # 为了兼容前端，直接返回data字段的内容
        response_data = {
            'status': 'success',
            'timestamp': datetime.now().isoformat()
        }
        # 将data字段的内容合并到顶层
        response_data.update(data)
        # 保留summary作为额外信息
        response_data['_summary'] = summary
        
        return jsonify(response_data)
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/tokens/report/<date_str>', methods=['GET'])
def get_token_report(date_str):
    """获取token报告"""
    try:
        from token_tracker import TokenTracker
        tracker = TokenTracker()
        
        report = tracker.generate_report(date_str)
        
        return jsonify({
            'status': 'success',
            'report': report,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/tokens/history/<days>', methods=['GET'])
def get_token_history(days):
    """获取历史token数据 - 修复版：查询token_daily_consumption表"""
    try:
        import datetime as dt
        
        # 获取最近N天的数据
        try:
            days_int = int(days)
            if days_int > 365:
                days_int = 365  # 限制最多一年
        except:
            days_int = 30  # 默认30天
        
        end_date = dt.datetime.now().date()
        start_date = end_date - dt.timedelta(days=days_int - 1)
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 获取每日汇总数据 - 从token_daily_consumption表
        cursor.execute('''
            SELECT 
                date,
                development_tokens,
                search_tokens,
                communication_tokens,
                other_tokens,
                total_tokens
            FROM token_daily_consumption 
            WHERE date >= ? AND date <= ?
            ORDER BY date
        ''', (start_date.isoformat(), end_date.isoformat()))
        
        history = []
        for row in cursor.fetchall():
            history.append({
                'date': row[0],
                'dev_tokens': row[1] or 0,
                'search_tokens': row[2] or 0,
                'comm_tokens': row[3] or 0,
                'other_tokens': row[4] or 0,
                'total_tokens': row[5] or 0
            })
        
        # 如果没有数据，尝试从token_usage表获取
        if not history:
            cursor.execute('''
                SELECT 
                    date,
                    SUM(CASE WHEN category = 'development' THEN total_tokens ELSE 0 END) as dev_tokens,
                    SUM(CASE WHEN category = 'search' THEN total_tokens ELSE 0 END) as search_tokens,
                    SUM(CASE WHEN category = 'communication' THEN total_tokens ELSE 0 END) as comm_tokens,
                    SUM(CASE WHEN category NOT IN ('development', 'search', 'communication') THEN total_tokens ELSE 0 END) as other_tokens,
                    SUM(total_tokens) as total_tokens
                FROM token_usage 
                WHERE date >= ? AND date <= ?
                GROUP BY date
                ORDER BY date
            ''', (start_date.isoformat(), end_date.isoformat()))
            
            for row in cursor.fetchall():
                history.append({
                    'date': row[0],
                    'dev_tokens': row[1] or 0,
                    'search_tokens': row[2] or 0,
                    'comm_tokens': row[3] or 0,
                    'other_tokens': row[4] or 0,
                    'total_tokens': row[5] or 0
                })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'days': days_int,
            'start_date': start_date.isoformat(),
            'end_date': end_date.isoformat(),
            'history': history,
            'timestamp': datetime.now().isoformat(),
            'data_source': 'token_daily_consumption' if history else 'token_usage'
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

# 自动打分同步功能
import re

def extract_score_from_text(text):
    """从文本中提取打分信息"""
    patterns = [
        r'([+-]?\d+)\s*分',          # "-10分", "+5分"
        r'扣\s*(\d+)\s*分',          # "扣10分"
        r'加\s*(\d+)\s*分',          # "加5分"
        r'减\s*(\d+)\s*分',          # "减10分"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            score_str = match.group(1)
            if pattern.startswith('扣') or pattern.startswith('减'):
                score = -int(score_str)
            elif pattern.startswith('加'):
                score = int(score_str)
            else:
                score = int(score_str)
            
            reason_start = match.end()
            reason = text[reason_start:].strip()
            if not reason:
                reason = "未说明原因"
            
            # 确定类别
            category = determine_score_category(reason)
            
            return {
                'score': score,
                'reason': reason,
                'category': category
            }
    
    return None

def determine_score_category(reason):
    """根据原因确定类别"""
    reason_lower = reason.lower()
    
    if any(word in reason_lower for word in ['token', '统计', '消耗']):
        return 'token管理'
    elif any(word in reason_lower for word in ['ui', '设计', '页面', '界面']):
        return 'UI设计'
    elif any(word in reason_lower for word in ['功能', '按钮', '扫描', '备注']):
        return '功能管理'
    elif any(word in reason_lower for word in ['操作', '误操作', '错误']):
        return '操作规范'
    elif any(word in reason_lower for word in ['记忆', '忘记', '要求', 'soul', 'memory']):
        return '记忆要求'
    elif any(word in reason_lower for word in ['沟通', '回复', '说话', '简洁']):
        return '沟通'
    elif any(word in reason_lower for word in ['时间', '延迟', '拖延', '第一时间']):
        return '时间管理'
    else:
        return '其他'

def calculate_week_number_by_date(date_obj):
    """根据日期计算周数（新规则：w1=1-7日, w2=8-14日, w3=15-21日, w4=22日-月底）"""
    day = date_obj.day
    
    if 1 <= day <= 7:
        return 1  # w1
    elif 8 <= day <= 14:
        return 2  # w2
    elif 15 <= day <= 21:
        return 3  # w3
    else:
        return 4  # w4

@app.route('/api/scores/auto-sync', methods=['POST'])
def auto_sync_score():
    """自动同步打分API"""
    data = request.get_json()
    
    if not data or 'text' not in data:
        return jsonify({
            'success': False,
            'error': '缺少text字段'
        }), 400
    
    text = data['text']
    score_info = extract_score_from_text(text)
    
    if not score_info:
        return jsonify({
            'success': False,
            'message': '未检测到打分信息'
        })
    
    # 录入数据库
    now = datetime.now()
    year_month = now.strftime('%Y-%m')
    week_number = calculate_week_number_by_date(now)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO scores (year_month, week_number, score_change, reason, category, created_at)
        VALUES (?, ?, ?, ?, ?, datetime('now'))
    ''', (year_month, week_number, score_info['score'], score_info['reason'], score_info['category']))
    
    conn.commit()
    
    # 获取当前总分
    cursor.execute('SELECT SUM(score_change) FROM scores')
    total_score = cursor.fetchone()[0] or 0
    
    conn.close()
    
    return jsonify({
        'success': True,
        'score': score_info['score'],
        'reason': score_info['reason'],
        'category': score_info['category'],
        'total_score': total_score,
        'message': f'打分已同步: {score_info["score"]}分'
    })

def calculate_score_from_tokens(total_tokens):
    """根据Token消耗计算分数（基于数据库中的打分规则）"""
    # 将tokens转换为M单位以便计算
    tokens_m = total_tokens / 1000000
    
    # 从数据库获取最新的打分规则
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 获取所有启用的规则
    cursor.execute('''
        SELECT rule_name, min_value, max_value, score, description 
        FROM scoring_rules 
        WHERE is_active = 1 
        ORDER BY rule_name, min_value
    ''')
    
    rules = cursor.fetchall()
    conn.close()
    
    # 首先检查扣分规则
    penalty_rules = [r for r in rules if r['rule_name'] == '扣分规则']
    for rule in penalty_rules:
        min_val = rule['min_value'] if rule['min_value'] is not None else float('-inf')
        max_val = rule['max_value'] if rule['max_value'] is not None else float('inf')
        
        # 处理边界情况
        if max_val is None:  # 没有上限，如 >20M
            if tokens_m > min_val:
                return rule['score']
        else:
            # 包含边界值：min_val <= tokens_m <= max_val
            if min_val <= tokens_m <= max_val:
                return rule['score']
    
    # 然后检查中性规则
    neutral_rules = [r for r in rules if r['rule_name'] == '中性规则']
    for rule in neutral_rules:
        min_val = rule['min_value'] if rule['min_value'] is not None else float('-inf')
        max_val = rule['max_value'] if rule['max_value'] is not None else float('inf')
        
        if min_val <= tokens_m <= max_val:
            return rule['score']
    
    # 最后检查奖励规则
    reward_rules = [r for r in rules if r['rule_name'] == '奖励规则']
    for rule in reward_rules:
        min_val = rule['min_value'] if rule['min_value'] is not None else float('-inf')
        max_val = rule['max_value'] if rule['max_value'] is not None else float('inf')
        
        if min_val <= tokens_m <= max_val:
            return rule['score']
    
    # 默认返回0分
    return 0

@app.route('/api/scores/recalculate-date', methods=['POST'])
def recalculate_score_for_date():
    """重新计算指定日期的分数（基于Token消耗）"""
    try:
        data = request.get_json()
        
        if not data or 'date' not in data:
            return jsonify({
                'success': False,
                'error': '缺少date字段'
            }), 400
        
        date_str = data['date']
        
        # 1. 获取该日期的Token消耗
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT total_tokens FROM token_daily_consumption 
            WHERE date = ?
        ''', (date_str,))
        
        result = cursor.fetchone()
        
        if not result:
            return jsonify({
                'success': False,
                'error': f'未找到{date_str}的Token消耗记录'
            })
        
        total_tokens = result['total_tokens']
        
        # 2. 根据最新的打分标准计算分数
        score = calculate_score_from_tokens(total_tokens)
        
        # 3. 检查是否已有该日期的打分记录
        cursor.execute('''
            SELECT id, score_change, reason FROM scores 
            WHERE reason LIKE ? AND created_at LIKE ?
            ORDER BY created_at DESC LIMIT 1
        ''', (f'%{date_str}%', f'{date_str}%'))
        
        existing_score = cursor.fetchone()
        
        if existing_score:
            # 更新现有记录
            cursor.execute('''
                UPDATE scores 
                SET score_change = ?, 
                    reason = ?
                WHERE id = ?
            ''', (
                score,
                f'{date_str} Token消耗: {total_tokens:,} tokens (重新计算)',
                existing_score['id']
            ))
            action = 'updated'
            old_score = existing_score['score_change']
        else:
            # 创建新记录
            now = datetime.now()
            year_month = now.strftime('%Y-%m')
            week_number = calculate_week_number_by_date(now)
            
            cursor.execute('''
                INSERT INTO scores (year_month, week_number, score_change, reason, category, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (
                year_month,
                week_number,
                score,
                f'{date_str} Token消耗: {total_tokens:,} tokens',
                'token管理',
                now
            ))
            action = 'created'
            old_score = None
        
        conn.commit()
        
        # 4. 获取当前总分
        cursor.execute('SELECT SUM(score_change) FROM scores')
        total_score = cursor.fetchone()[0] or 0
        
        conn.close()
        
        return jsonify({
            'success': True,
            'date': date_str,
            'total_tokens': total_tokens,
            'tokens_m': total_tokens / 1000000,
            'score': score,
            'old_score': old_score,
            'total_score': total_score,
            'action': action,
            'message': f'{date_str}的分数已{"更新" if action == "updated" else "创建"}: {score}分'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/scores/recalculate-all', methods=['POST'])
def recalculate_all_scores():
    """重新计算所有日期的分数（基于Token消耗）"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 1. 获取所有有Token消耗记录的日期
        cursor.execute('''
            SELECT date, total_tokens FROM token_daily_consumption 
            ORDER BY date DESC
        ''')
        
        token_records = cursor.fetchall()
        
        if not token_records:
            return jsonify({
                'success': False,
                'error': '未找到任何Token消耗记录'
            })
        
        results = []
        updated_count = 0
        created_count = 0
        
        # 2. 为每个日期重新计算分数
        for record in token_records:
            date_str = record['date']
            total_tokens = record['total_tokens']
            
            # 计算分数
            score = calculate_score_from_tokens(total_tokens)
            
            # 检查是否已有该日期的打分记录
            cursor.execute('''
                SELECT id, score_change, reason FROM scores 
                WHERE reason LIKE ? AND created_at LIKE ?
                ORDER BY created_at DESC LIMIT 1
            ''', (f'%{date_str}%', f'{date_str}%'))
            
            existing_score = cursor.fetchone()
            
            if existing_score:
                # 更新现有记录
                cursor.execute('''
                    UPDATE scores 
                    SET score_change = ?, 
                        reason = ?
                    WHERE id = ?
                ''', (
                    score,
                    f'{date_str} Token消耗: {total_tokens:,} tokens (重新计算)',
                    existing_score['id']
                ))
                action = 'updated'
                old_score = existing_score['score_change']
                updated_count += 1
            else:
                # 创建新记录
                date_obj = datetime.strptime(date_str, '%Y-%m-%d')
                year_month = date_obj.strftime('%Y-%m')
                week_number = calculate_week_number_by_date(date_obj)
                
                cursor.execute('''
                    INSERT INTO scores (year_month, week_number, score_change, reason, category, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    year_month,
                    week_number,
                    score,
                    f'{date_str} Token消耗: {total_tokens:,} tokens',
                    'token管理',
                    date_obj
                ))
                action = 'created'
                old_score = None
                created_count += 1
            
            results.append({
                'date': date_str,
                'total_tokens': total_tokens,
                'tokens_m': total_tokens / 1000000,
                'score': score,
                'old_score': old_score,
                'action': action
            })
        
        conn.commit()
        
        # 3. 获取当前总分
        cursor.execute('SELECT SUM(score_change) FROM scores')
        total_score = cursor.fetchone()[0] or 0
        
        conn.close()
        
        return jsonify({
            'success': True,
            'total_records': len(token_records),
            'updated_count': updated_count,
            'created_count': created_count,
            'total_score': total_score,
            'results': results,
            'message': f'已完成{len(token_records)}个日期的分数重新计算，更新{updated_count}条，新增{created_count}条，当前总分: {total_score}分'
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/scores/current', methods=['GET'])
def get_current_score():
    """获取当前总分"""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT SUM(score_change) FROM scores')
    total_score = cursor.fetchone()[0] or 0
    conn.close()
    
    return jsonify({
        'success': True,
        'total_score': total_score,
        'timestamp': datetime.now().isoformat()
    })

@app.route('/tokens')
def token_stats():
    """Token统计页面"""
    return render_template('tokens_enhanced.html')

@app.route('/api/daily/<date_str>', methods=['GET'])
def get_daily_data(date_str):
    """获取日报数据"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 获取新闻
        cursor.execute('''
            SELECT id, title, summary, content, source, source_name, url, category, published_at
            FROM daily_news 
            WHERE date = ?
            ORDER BY published_at DESC
        ''', (date_str,))
        
        news = []
        for row in cursor.fetchall():
            news.append({
                'id': row[0],
                'title': row[1],
                'summary': row[2],
                'content': row[3],  # 添加content字段
                'source': row[4],
                'source_name': row[5],
                'url': row[6],
                'category': row[7],
                'published_at': row[8]
            })
        
        # 获取股票
        cursor.execute('''
            SELECT id, symbol, name, price, change, change_percent, volume, market_cap, analysis, last_updated
            FROM daily_stocks 
            WHERE date = ?
            ORDER BY symbol
        ''', (date_str,))
        
        stocks = []
        for row in cursor.fetchall():
            stocks.append({
                'id': row[0],
                'symbol': row[1],
                'name': row[2],
                'price': float(row[3]) if row[3] else 0,
                'change': float(row[4]) if row[4] else 0,
                'change_percent': float(row[5]) if row[5] else 0,
                'volume': row[6],
                'market_cap': row[7],
                'analysis': row[8],
                'last_updated': row[9]
            })
        
        # 获取天气
        cursor.execute('''
            SELECT location, temperature, feels_like, condition, humidity, wind_speed,
                   wind_direction, lunar_date, holiday, reminder, forecast, created_at
            FROM daily_weather_v2
            WHERE date = ? AND (location LIKE '%廊坊%' OR location LIKE '%Langfang%')
            LIMIT 1
        ''', (date_str,))

        weather_row = cursor.fetchone()
        weather = None
        if weather_row:
            import json
            forecast_data = {}
            if weather_row[10]:
                try:
                    forecast_data = json.loads(weather_row[10])
                except:
                    forecast_data = {'today': str(weather_row[10])}

            weather = {
                'location': weather_row[0],
                'temperature': weather_row[1],
                'feels_like': weather_row[2],
                'condition': weather_row[3],
                'humidity': weather_row[4],
                'wind_speed': weather_row[5],
                'wind_direction': weather_row[6],
                'lunar_date': weather_row[7],
                'holiday': weather_row[8],
                'memo': weather_row[9],
                'forecast': forecast_data,
                'created_at': weather_row[11]
            }


        # 获取改进报告
        cursor.execute('''
            SELECT title, content, score_change, created_at
            FROM daily_improvements 
            WHERE date = ?
            ORDER BY created_at DESC
            LIMIT 1
        ''', (date_str,))
        
        improvement_row = cursor.fetchone()
        improvements = []
        if improvement_row:
            improvements.append({
                'title': improvement_row[0],
                'content': improvement_row[1],
                
                'score_change': improvement_row[2],
                'created_at': improvement_row[3]
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'date': date_str,
            'news': news,
            'stocks': stocks,
            'weather': weather,
            'improvements': improvements,
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/api/daily/dates', methods=['GET'])
def get_daily_dates():
    """获取有日报的日期列表"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 从各个表获取有数据的日期
        dates_set = set()
        
        # 从新闻表
        cursor.execute('SELECT DISTINCT date FROM daily_news ORDER BY date DESC LIMIT 30')
        dates_set.update(row[0] for row in cursor.fetchall())
        
        # 从股票表
        cursor.execute('SELECT DISTINCT date FROM daily_stocks ORDER BY date DESC LIMIT 30')
        dates_set.update(row[0] for row in cursor.fetchall())
        
        # 从改进报告表
        cursor.execute('SELECT DISTINCT date FROM daily_improvements ORDER BY date DESC LIMIT 30')
        dates_set.update(row[0] for row in cursor.fetchall())
        
        # 从天气表
        cursor.execute('SELECT DISTINCT date FROM daily_weather ORDER BY date DESC LIMIT 30')
        dates_set.update(row[0] for row in cursor.fetchall())
        
        conn.close()
        
        # 排序并格式化
        dates = sorted(dates_set, reverse=True)
        
        return jsonify({
            'status': 'success',
            'dates': dates,
            'count': len(dates),
            'timestamp': datetime.now().isoformat()
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

def send_weixin_message(message):
    """发送微信消息（供脚本调用）"""
    try:
        # 这里实现消息发送逻辑
        # 由于OpenClaw消息发送需要特定配置，我们先记录到日志
        print(f"[微信消息] 长度: {len(message)} 字符")
        print(f"[微信消息] 预览: {message[:100]}...")
        
        # 保存到文件供后续处理
        with open('/tmp/weixin_message_to_send.txt', 'w', encoding='utf-8') as f:
            f.write(message)
        
        return True
    except Exception as e:
        print(f"[微信消息] 发送失败: {e}")
        return False

@app.route('/daily')
def daily_report():
    """日报页面（两列布局：左侧日期标签，右侧折叠内容）"""
    return render_template('daily_fixed.html')

@app.route('/daily-old')
def daily_old_page():
    """旧版日报页面"""
    return render_template('daily_static_today.html')

# 这个
# ─── Pablo ↔ Pbee 对话记录 API ─────────────────────────────────




@app.route('/api/tokens/calibrated')
def get_calibrated_tokens():
    import sqlite3
    conn = sqlite3.connect('scores.db')
    cursor = conn.cursor()
    
    cursor.execute('SELECT deepseek_total, my_total, days FROM token_stats_calibrated')
    row = cursor.fetchone()
    deepseek, my, days = row if row else (0, 0, 0)
    
    conn.close()
    
    return jsonify({
        'status': 'success',
        'calibrated': {
            'deepseek_total': deepseek,
            'my_report_total': my,
            'gap': deepseek - my,
            'gap_percent': round(((deepseek - my) / deepseek * 100), 1) if deepseek > 0 else 0,
            'days': days
        }
    })

# ==================== 日报历史API ====================
@app.route('/api/daily/dates')
def get_daily_history():
    """获取日报历史记录"""
    try:
        import sqlite3
        
        db_path = 'scores.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 获取历史数据
        cursor.execute('''
            SELECT date,
                   (SELECT COUNT(*) FROM daily_news WHERE date = d.date) as news_count,
                   (SELECT COUNT(*) FROM daily_stocks WHERE date = d.date) as stocks_count,
                   (SELECT COUNT(*) FROM daily_weather_v2 WHERE date = d.date) as weather_count,
                   (SELECT COUNT(*) FROM daily_improvements WHERE date = d.date) as improvements_count
            FROM (SELECT DISTINCT date FROM daily_news
                  UNION SELECT DISTINCT date FROM daily_stocks
                  UNION SELECT DISTINCT date FROM daily_weather_v2
                  UNION SELECT DISTINCT date FROM daily_improvements) d
            ORDER BY date DESC
        ''')
        
        rows = cursor.fetchall()
        
        history = []
        for row in rows:
            date, news_count, stocks_count, weather_count, improvements_count = row
            total = news_count + stocks_count + weather_count + improvements_count
            history.append({
                'date': date,
                'news_count': news_count,
                'stocks_count': stocks_count,
                'weather_count': weather_count,
                'improvements_count': improvements_count,
                'total_items': total
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'history': history,
            'total_days': len(history)
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'获取历史失败: {str(e)}'
        }), 500

@app.route('/api/daily/full/<date_str>')
def get_full_daily(date_str):
    """获取指定日期的完整日报数据"""
    try:
        import sqlite3
        
        db_path = 'scores.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. 天气数据
        cursor.execute('SELECT * FROM daily_weather_v2 WHERE date = ? ORDER BY created_at DESC LIMIT 1', (date_str,))
        weather_row = cursor.fetchone()
        weather = None
        if weather_row:
            weather = {
                'location': weather_row[2],
                'temperature': weather_row[3],
                'feels_like': weather_row[4],
                'condition': weather_row[5],
                'humidity': weather_row[6],
                'wind_speed': weather_row[7],
                'wind_direction': weather_row[8],
                'lunar_date': weather_row[9],
                'holiday': weather_row[10],
                'reminder': weather_row[11],
                'forecast': weather_row[12]
            }
        
        # 2. 新闻数据
        cursor.execute('SELECT title, content, source, url FROM daily_news WHERE date = ? ORDER BY created_at DESC', (date_str,))
        news = []
        for row in cursor.fetchall():
            news.append({
                'title': row[0],
                'content': row[1],
                'source': row[2],
                'url': row[3]
            })
        
        # 3. 股票数据
        cursor.execute('SELECT symbol, name, price, change_percent, analysis, prediction FROM daily_stocks WHERE date = ? ORDER BY created_at DESC', (date_str,))
        stocks = []
        for row in cursor.fetchall():
            stocks.append({
                'symbol': row[0],
                'name': row[1],
                'price': row[2],
                'change_percent': row[3],
                'analysis': row[4],
                'prediction': row[5]
            })
        
        # 4. 改进报告
        cursor.execute('SELECT title, content, score_change, created_at FROM daily_improvements WHERE date = ? ORDER BY created_at DESC', (date_str,))
        improvements = []
        for row in cursor.fetchall():
            improvements.append({
                'title': row[0],
                'content': row[1],
                'score_change': row[2],
                'created_at': row[3]
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'date': date_str,
            'weather': weather,
            'news': news,
            'stocks': stocks,
            'improvements': improvements,
            'summary': {
                'news_count': len(news),
                'stocks_count': len(stocks),
                'improvements_count': len(improvements),
                'has_weather': weather is not None,
                'total_items': len(news) + len(stocks) + len(improvements) + (1 if weather else 0)
            }
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'获取日报数据失败: {str(e)}'
        }), 500



@app.route('/api/daily/fixed/<date_str>')
def get_daily_fixed(date_str):
    """修复版日报API"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        result = {
            'date': date_str,
            'weather': [],
            'news': [],
            'stocks': [],
            'improvements': []
        }
        
        # 1. 天气数据 (daily_weather_v2)
        try:
            cursor.execute('SELECT location, temperature, feels_like, condition, humidity, wind_speed, wind_direction, lunar_date, holiday, reminder, forecast FROM daily_weather_v2 WHERE date = ? ORDER BY created_at DESC LIMIT 1', (date_str,))
            weather_row = cursor.fetchone()
            if weather_row:
                result['weather'].append({
                    'location': weather_row[0],
                    'temperature': weather_row[1],
                    'feels_like': weather_row[2],
                    'condition': weather_row[3],
                    'humidity': weather_row[4],
                    'wind_speed': weather_row[5],
                    'wind_direction': weather_row[6],
                    'lunar_date': weather_row[7],
                    'holiday': weather_row[8],
                    'reminder': weather_row[9],
                    'forecast': weather_row[10]
                })
        except Exception as e:
            print(f"天气查询错误: {e}")
        
        # 2. 新闻数据
        try:
            cursor.execute('SELECT title, content, source, url FROM daily_news WHERE date = ? ORDER BY created_at DESC', (date_str,))
            for row in cursor.fetchall():
                result['news'].append({
                    'title': row[0],
                    'content': row[1],
                    'source': row[2],
                    'url': row[3]
                })
        except Exception as e:
            print(f"新闻查询错误: {e}")
        
        # 3. 股票数据 (简化查询)
        try:
            cursor.execute('SELECT symbol, name, price, change_percent, analysis FROM daily_stocks WHERE date = ? ORDER BY symbol', (date_str,))
            for row in cursor.fetchall():
                result['stocks'].append({
                    'symbol': row[0],
                    'name': row[1],
                    'price': row[2],
                    'change_percent': row[3],
                    'analysis': row[4]
                })
        except Exception as e:
            print(f"股票查询错误: {e}")
        
        # 4. 改进报告
        try:
            cursor.execute('SELECT title, content, score_change FROM daily_improvements WHERE date = ? ORDER BY created_at DESC', (date_str,))
            for row in cursor.fetchall():
                result['improvements'].append({
                    'title': row[0],
                    'content': row[1],
                    'score_change': row[2]
                })
        except Exception as e:
            print(f"改进报告查询错误: {e}")
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'date': date_str,
            'data': result,
            'summary': {
                'weather_count': len(result['weather']),
                'news_count': len(result['news']),
                'stocks_count': len(result['stocks']),
                'improvements_count': len(result['improvements'])
            }
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'获取日报数据失败: {str(e)}'
        }), 500

# ==================== 直接数据库查询API（修复日报数据问题） ====================
@app.route('/api/daily/direct/<date_str>')
def get_daily_direct(date_str):
    """直接数据库查询日报数据（修复原API问题）"""
    try:
        import sqlite3
        
        db_path = 'scores.db'
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. 天气数据（使用daily_weather_v2表）
        cursor.execute('''
            SELECT location, temperature, feels_like, condition, humidity, 
                   wind_speed, wind_direction, lunar_date, holiday, reminder, forecast
            FROM daily_weather_v2 
            WHERE date = ?
            ORDER BY created_at DESC LIMIT 1
        ''', (date_str,))
        
        weather = []
        for row in cursor.fetchall():
            weather.append({
                'location': row[0],
                'temperature': row[1],
                'feels_like': row[2],
                'condition': row[3],
                'humidity': row[4],
                'wind_speed': row[5],
                'wind_direction': row[6],
                'lunar_date': row[7],
                'holiday': row[8],
                'reminder': row[9],
                'forecast': row[10]
            })
        
        # 2. 新闻数据
        cursor.execute('''
            SELECT title, content, source, source_name, url
            FROM daily_news 
            WHERE date = ?
            ORDER BY published_at DESC
        ''', (date_str,))
        
        news = []
        for row in cursor.fetchall():
            news.append({
                'title': row[0],
                'content': row[1],
                'source': row[2],
                'source_name': row[3],
                'url': row[4]
            })
        
        # 3. 股票数据
        cursor.execute('''
            SELECT symbol, name, price, change, change_percent, analysis
            FROM daily_stocks 
            WHERE date = ?
            ORDER BY symbol
        ''', (date_str,))
        
        stocks = []
        for row in cursor.fetchall():
            stocks.append({
                'symbol': row[0],
                'name': row[1],
                'price': float(row[2]) if row[2] else 0,
                'change': float(row[3]) if row[3] else 0,
                'change_percent': float(row[4]) if row[4] else 0,
                'analysis': row[5]
            })
        
        # 4. 改进报告
        cursor.execute('''
            SELECT title, content, score_change
            FROM daily_improvements 
            WHERE date = ?
            ORDER BY created_at DESC
        ''', (date_str,))
        
        improvements = []
        for row in cursor.fetchall():
            improvements.append({
                'title': row[0],
                'content': row[1],
                'score_change': row[2]
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'date': date_str,
            'weather': weather,
            'news': news,
            'stocks': stocks,
            'improvements': improvements,
            'summary': {
                'weather_count': len(weather),
                'news_count': len(news),
                'stocks_count': len(stocks),
                'improvements_count': len(improvements)
            }
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'直接查询失败: {str(e)}'
        }), 500

# 更新日报页面路由使用直接查询版本
@app.route('/daily-fixed')
def daily_fixed_page():
    """修复版日报页面（使用直接数据库查询）"""
    return render_template('daily_direct_db.html')

# ==================== 今日Token消耗分析页面 ====================
@app.route('/data-stats-today')
def data_stats_today():
    """今日Token消耗分析页面"""
    return render_template('data_stats_today.html')

# ==================== Token消耗数据API ====================
@app.route('/api/token/today')
def get_token_today():
    """获取今日token消耗数据"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        today = datetime.now().strftime('%Y-%m-%d')
        
        # 查询今日消耗
        cursor.execute('''
            SELECT date, total_tokens, development_tokens, search_tokens, 
                   communication_tokens, other_tokens, notes
            FROM token_daily_consumption 
            WHERE date = ?
        ''', (today,))
        
        row = cursor.fetchone()
        
        if row:
            data = {
                'date': row[0],
                'total_tokens': row[1],
                'development_tokens': row[2],
                'search_tokens': row[3],
                'communication_tokens': row[4],
                'other_tokens': row[5],
                'notes': row[6],
                'total_m': round(row[1] / 1000000, 2),
                'development_percent': round(row[2] / row[1] * 100, 1) if row[1] > 0 else 0,
                'search_percent': round(row[3] / row[1] * 100, 1) if row[1] > 0 else 0,
                'communication_percent': round(row[4] / row[1] * 100, 1) if row[1] > 0 else 0,
                'other_percent': round(row[5] / row[1] * 100, 1) if row[1] > 0 else 0
            }
        else:
            data = {
                'date': today,
                'total_tokens': 0,
                'total_m': 0,
                'message': '今日暂无token消耗记录'
            }
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'data': data
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'获取token数据失败: {str(e)}'
        }), 500

@app.route('/api/token/history')
def get_token_history_api():
    """获取token消耗历史数据"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT date, total_tokens, development_tokens, search_tokens, 
                   communication_tokens, other_tokens
            FROM token_daily_consumption 
            ORDER BY date DESC
            LIMIT 30
        ''')
        
        history = []
        for row in cursor.fetchall():
            history.append({
                'date': row[0],
                'total_tokens': row[1],
                'total_m': round(row[1] / 1000000, 2),
                'development_tokens': row[2],
                'search_tokens': row[3],
                'communication_tokens': row[4],
                'other_tokens': row[5]
            })
        
        conn.close()
        
        return jsonify({
            'status': 'success',
            'count': len(history),
            'data': history
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'获取历史数据失败: {str(e)}'
        }), 500

# ==================== Token自动管理页面 ====================
@app.route('/tokens-auto')
def tokens_auto():
    """Token自动管理页面"""
    return render_template('tokens_auto.html')

# 导入token报告生成器
try:
    from token_report_simple import generate_token_report
except ImportError:
    # 如果导入失败，创建简单版本
    @app.route('/api/token/submit', methods=['POST'])
    def submit_token_data():
        """提交token数据API"""
        try:
            import json
            data = request.get_json()
            
            if not data or 'total_tokens' not in data:
                return jsonify({
                    'success': False,
                    'error': '缺少必要参数：total_tokens'
                }), 400
            
            total_tokens = int(data['total_tokens'])
            
            # 使用token报告生成器
            from token_report_simple import generate_token_report
            report = generate_token_report(total_tokens)
            
            if report['success']:
                return jsonify(report)
            else:
                return jsonify({
                    'success': False,
                    'error': report.get('error', '生成报告失败')
                }), 500
                
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'提交失败：{str(e)}'
            }), 500
    
    @app.route('/api/token/quick/<int:total_tokens>')
    def quick_token_submit(total_tokens):
        """快速提交token数据"""
        try:
            from token_report_simple import generate_token_report
            report = generate_token_report(total_tokens)
            
            if report['success']:
                return jsonify(report)
            else:
                return jsonify({
                    'success': False,
                    'error': report.get('error', '生成报告失败')
                }), 500
                
        except Exception as e:
            return jsonify({
                'success': False,
                'error': f'快速提交失败：{str(e)}'
            }), 500

@app.route('/api/network/schedule', methods=['GET'])
def get_network_schedules():
    """获取所有网络扫描定时设置"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        # 获取所有定时任务
        cursor.execute('''
            SELECT id, task_type, enabled, schedule_expr, schedule_tz, description, last_updated
            FROM network_schedule
            ORDER BY task_type
        ''')
        
        schedules = []
        for row in cursor.fetchall():
            schedule = {
                'id': row[0],
                'task_type': row[1],  # 'quick' 或 'full'
                'enabled': bool(row[2]),
                'schedule_expr': row[3],
                'schedule_tz': row[4],
                'description': row[5],
                'last_updated': row[6]
            }
            
            # 计算下次执行时间
            schedule['next_execution'] = calculate_next_execution(schedule['schedule_expr'], schedule['schedule_tz'])
            schedules.append(schedule)
        
        conn.close()
        
        # 确保有两种类型的任务
        task_types = {s['task_type'] for s in schedules}
        if 'quick' not in task_types:
            schedules.append(create_default_schedule('quick'))
        if 'full' not in task_types:
            schedules.append(create_default_schedule('full'))
        
        return jsonify({
            'success': True,
            'data': schedules,
            'count': len(schedules)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'获取定时设置失败：{str(e)}'
        }), 500

@app.route('/api/network/schedule/<task_type>', methods=['GET', 'PUT', 'DELETE'])
def network_schedule_task_api(task_type):
    """特定类型网络扫描定时设置API"""
    try:
        # 验证任务类型
        if task_type not in ['quick', 'full']:
            return jsonify({
                'success': False,
                'error': '无效的任务类型，应为 quick 或 full'
            }), 400
        
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        if request.method == 'GET':
            # 获取特定类型的定时设置
            cursor.execute('''
                SELECT id, task_type, enabled, schedule_expr, schedule_tz, description, last_updated
                FROM network_schedule
                WHERE task_type = ?
            ''', (task_type,))
            
            row = cursor.fetchone()
            if row:
                schedule = {
                    'id': row[0],
                    'task_type': row[1],
                    'enabled': bool(row[2]),
                    'schedule_expr': row[3],
                    'schedule_tz': row[4],
                    'description': row[5],
                    'last_updated': row[6]
                }
                schedule['next_execution'] = calculate_next_execution(schedule['schedule_expr'], schedule['schedule_tz'])
            else:
                # 创建默认设置
                schedule = create_default_schedule(task_type)
            
            conn.close()
            return jsonify({
                'success': True,
                'data': schedule
            })
        
        elif request.method == 'PUT':
            # 更新特定类型的定时设置
            data = request.get_json()
            if not data:
                return jsonify({
                    'success': False,
                    'error': '缺少请求数据'
                }), 400
            
            enabled = data.get('enabled', True)
            schedule_expr = data.get('schedule_expr', '30 18 * * *' if task_type == 'quick' else '0 6 * * 0')
            schedule_tz = data.get('schedule_tz', 'Asia/Shanghai')
            description = data.get('description', '')
            
            # 验证cron表达式
            if not schedule_expr or len(schedule_expr.split()) != 5:
                return jsonify({
                    'success': False,
                    'error': '无效的cron表达式，格式应为: 分钟 小时 日 月 星期'
                }), 400
            
            # 检查是否存在记录
            cursor.execute('SELECT COUNT(*) FROM network_schedule WHERE task_type = ?', (task_type,))
            count = cursor.fetchone()[0]
            
            if count == 0:
                # 创建新记录
                if not description:
                    description = f'{"快速检查" if task_type == "quick" else "完整扫描"}网络设备'
                
                cursor.execute('''
                    INSERT INTO network_schedule (task_type, enabled, schedule_expr, schedule_tz, description, last_updated)
                    VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ''', (task_type, enabled, schedule_expr, schedule_tz, description))
            else:
                # 更新记录
                cursor.execute('''
                    UPDATE network_schedule 
                    SET enabled = ?, schedule_expr = ?, schedule_tz = ?, description = ?, last_updated = datetime('now', 'localtime')
                    WHERE task_type = ?
                ''', (enabled, schedule_expr, schedule_tz, description, task_type))
            
            conn.commit()
            conn.close()
            
            # 更新OpenClaw cron配置
            update_openclaw_cron_config(task_type, enabled, schedule_expr, schedule_tz)
            
            return jsonify({
                'success': True,
                'message': f'{task_type}定时设置已更新'
            })
        
        elif request.method == 'DELETE':
            # 删除特定类型的定时设置（重置为默认）
            cursor.execute('DELETE FROM network_schedule WHERE task_type = ?', (task_type,))
            conn.commit()
            conn.close()
            
            # 重置OpenClaw cron配置
            default_expr = '30 18 * * *' if task_type == 'quick' else '0 6 * * 0'
            update_openclaw_cron_config(task_type, True, default_expr, 'Asia/Shanghai')
            
            return jsonify({
                'success': True,
                'message': f'{task_type}定时设置已重置为默认'
            })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'操作失败：{str(e)}'
        }), 500

@app.route('/api/scores/weekly-trend')
def get_weekly_trend():
    """获取按周统计的分数变化趋势"""
    conn = get_db_connection()
    
    # 获取按周累计的分数变化
    weekly_data = conn.execute('''
        SELECT 
            year_month,
            week_number,
            SUM(score_change) as weekly_total,
            COUNT(*) as record_count
        FROM scores 
        GROUP BY year_month, week_number
        ORDER BY year_month, week_number
    ''').fetchall()
    
    # 转换为前端需要的格式
    result = {
        'labels': [],  # 周标签，如"2026-04-w1"
        'data': [],    # 每周累计分数
        'cumulative': [],  # 累计总分
        'details': []  # 详细数据
    }
    
    cumulative_total = 0
    
    for row in weekly_data:
        week_label = f"{row['year_month']}-w{row['week_number']}"
        weekly_total = row['weekly_total'] or 0
        cumulative_total += weekly_total
        
        result['labels'].append(week_label)
        result['data'].append(weekly_total)
        result['cumulative'].append(cumulative_total)
        result['details'].append({
            'year_month': row['year_month'],
            'week_number': row['week_number'],
            'weekly_total': weekly_total,
            'record_count': row['record_count'],
            'cumulative_total': cumulative_total
        })
    
    # 添加当前总分
    total_score = conn.execute('SELECT SUM(score_change) FROM scores').fetchone()[0] or 0
    result['current_total'] = total_score
    
    return jsonify(result)

@app.route('/api/code-modules/stats')
def get_code_modules_stats():
    """获取代码模块复用统计"""
    conn = get_db_connection()
    
    # 获取模块总数和统计
    modules_stats = conn.execute('''
        SELECT 
            COUNT(*) as total_modules,
            SUM(reuse_count) as total_reuse_count,
            SUM(estimated_tokens_saved) as total_tokens_saved,
            AVG(reuse_count) as avg_reuse_per_module,
            module_type,
            COUNT(*) as type_count,
            SUM(reuse_count) as type_reuse,
            SUM(estimated_tokens_saved) as type_tokens_saved
        FROM code_modules 
        GROUP BY module_type
        ORDER BY type_count DESC
    ''').fetchall()
    
    # 获取最常用的模块
    top_modules = conn.execute('''
        SELECT 
            module_name,
            module_type,
            reuse_count,
            estimated_tokens_saved,
            last_used,
            description,
            created_at
        FROM code_modules 
        ORDER BY reuse_count DESC, estimated_tokens_saved DESC
        LIMIT 10
    ''').fetchall()
    
    # 获取所有模块（用于列表展示）
    all_modules = conn.execute('''
        SELECT 
            id,
            module_name,
            module_type,
            reuse_count,
            estimated_tokens_saved,
            last_used,
            description,
            created_at,
            file_path,
            lines_of_code
        FROM code_modules 
        ORDER BY created_at DESC
    ''').fetchall()
    
    # 获取最近使用的模块
    recent_usage = conn.execute('''
        SELECT 
            m.module_name,
            m.module_type,
            u.usage_context,
            u.tokens_estimated_saved,
            u.usage_timestamp
        FROM module_usage u
        JOIN code_modules m ON u.module_id = m.id
        ORDER BY u.usage_timestamp DESC
        LIMIT 20
    ''').fetchall()
    
    # 计算总体统计
    total_stats = {
        'total_modules': modules_stats[0]['total_modules'] if modules_stats else 0,
        'total_reuse_count': modules_stats[0]['total_reuse_count'] if modules_stats else 0,
        'total_tokens_saved': modules_stats[0]['total_tokens_saved'] if modules_stats else 0,
        'avg_reuse_per_module': modules_stats[0]['avg_reuse_per_module'] if modules_stats else 0
    }
    
    # 按类型统计
    type_stats = []
    for stat in modules_stats:
        type_stats.append({
            'module_type': stat['module_type'],
            'count': stat['type_count'],
            'reuse_count': stat['type_reuse'] or 0,
            'tokens_saved': stat['type_tokens_saved'] or 0,
            'avg_reuse': round((stat['type_reuse'] or 0) / stat['type_count'], 1) if stat['type_count'] > 0 else 0
        })
    
    # 格式化结果
    result = {
        'success': True,
        'summary': total_stats,
        'by_type': type_stats,
        'modules': [
            {
                'id': m['id'],
                'name': m['module_name'],
                'type': m['module_type'],
                'reuse_count': m['reuse_count'],
                'tokens_saved': m['estimated_tokens_saved'],
                'last_used': m['last_used'],
                'created_at': m['created_at'],
                'description': m['description'],
                'file_path': m['file_path'],
                'lines_of_code': m['lines_of_code']
            }
            for m in all_modules
        ],
        'top_modules': [
            {
                'name': m['module_name'],
                'type': m['module_type'],
                'reuse_count': m['reuse_count'],
                'tokens_saved': m['estimated_tokens_saved'],
                'last_used': m['last_used'],
                'created_at': m['created_at'],
                'description': m['description']
            }
            for m in top_modules
        ],
        'recent_usage': [
            {
                'module_name': u['module_name'],
                'module_type': u['module_type'],
                'context': u['usage_context'],
                'tokens_saved': u['tokens_estimated_saved'],
                'timestamp': u['usage_timestamp']
            }
            for u in recent_usage
        ]
    }
    
    conn.close()
    return jsonify(result)

@app.route('/code-modules')
def code_modules_page():
    """代码模块复用统计页面"""
    return render_template('modules_enhanced_fixed.html')

@app.route('/api/code-modules/enhanced-stats')
def get_enhanced_module_stats():
    """获取增强版模块统计"""
    from module_tracker import tracker
    
    try:
        stats = tracker.get_module_stats()
        
        # 获取所有模块
        conn = get_db_connection()
        modules = conn.execute('''
            SELECT 
                id, module_name, module_type, file_path,
                reuse_count, estimated_tokens_saved,
                lines_of_code, created_at, last_used,
                description
            FROM code_modules
            ORDER BY reuse_count DESC, estimated_tokens_saved DESC
        ''').fetchall()
        
        # 按类型统计数量
        type_counts = {}
        for module in modules:
            module_type = module['module_type']
            type_counts[module_type] = type_counts.get(module_type, 0) + 1
        
        # 增强summary
        stats['summary']['by_type_counts'] = type_counts
        
        conn.close()
        
        return jsonify({
            'success': True,
            'summary': stats['summary'],
            'by_type': stats['by_type'],
            'top_modules': stats['top_modules'],
            'recent_usage': stats['recent_usage'],
            'modules': [dict(module) for module in modules]
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/code-modules/detail/<module_name>')
def get_module_detail(module_name):
    """获取模块详情"""
    from module_tracker import tracker
    
    try:
        conn = get_db_connection()
        
        # 获取模块基本信息
        module = conn.execute('''
            SELECT * FROM code_modules 
            WHERE module_name = ?
        ''', (module_name,)).fetchone()
        
        if not module:
            conn.close()
            return jsonify({'success': False, 'error': '模块不存在'}), 404
        
        # 获取使用历史
        usage_history = conn.execute('''
            SELECT 
                usage_context, tokens_estimated_saved,
                usage_timestamp, notes
            FROM module_usage
            WHERE module_id = ?
            ORDER BY usage_timestamp DESC
            LIMIT 50
        ''', (module['id'],)).fetchall()
        
        conn.close()
        
        return jsonify({
            'success': True,
            **dict(module),
            'usage_history': [dict(usage) for usage in usage_history]
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/code-modules/record-usage', methods=['POST'])
def record_module_usage():
    """记录模块使用"""
    from module_tracker import tracker
    
    try:
        data = request.get_json()
        
        if not data or 'module_name' not in data:
            return jsonify({'success': False, 'error': '缺少模块名称'}), 400
        
        success = tracker.record_usage(
            module_name=data['module_name'],
            usage_context=data.get('usage_context', 'development'),
            tokens_saved=data.get('tokens_saved', 100),
            notes=data.get('notes', '')
        )
        
        if success:
            return jsonify({'success': True, 'message': '使用记录保存成功'})
        else:
            return jsonify({'success': False, 'error': '模块不存在'}), 404
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/code-modules/scan', methods=['POST'])
def scan_project_modules():
    """扫描项目模块"""
    from module_tracker import tracker
    
    try:
        stats = tracker.auto_register_modules()
        
        return jsonify({
            'success': True,
            'message': '模块扫描完成',
            'stats': stats
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/code-modules/search')
def search_modules():
    """搜索模块"""
    from module_tracker import tracker
    
    try:
        keyword = request.args.get('keyword', '')
        module_type = request.args.get('type', '')
        min_reuse = int(request.args.get('min_reuse', 0))
        limit = int(request.args.get('limit', 100))
        
        results = tracker.search_modules(
            keyword=keyword,
            module_type=module_type,
            min_reuse=min_reuse,
            limit=limit
        )
        
        return jsonify({
            'success': True,
            'results': results,
            'count': len(results)
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/network/schedule/test', methods=['POST'])
def test_network_schedule():
    """测试网络扫描定时设置"""
    try:
        data = request.get_json() or {}
        scan_type = data.get('scan_type', 'quick')  # 默认为快速扫描
        
        # 立即执行一次网络扫描
        from network_scanner import NetworkScanner
        scanner = NetworkScanner()
        
        # 根据扫描类型设置扫描范围
        if scan_type == 'quick':
            # 快速扫描：只扫描前50个IP
            online_count, devices = scanner.scan_network(end=50)
            scan_desc = '快速扫描'
        else:
            # 完整扫描：扫描全部254个IP
            online_count, devices = scanner.scan_network()
            scan_desc = '完整扫描'
        
        return jsonify({
            'success': True,
            'message': f'{scan_desc}测试完成，发现 {online_count} 台在线设备',
            'data': {
                'online_count': online_count,
                'device_count': len(devices),
                'scan_type': scan_type,
                'scan_desc': scan_desc,
                'timestamp': datetime.now().isoformat()
            }
        })
        
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'测试失败：{str(e)}'
        }), 500

def calculate_next_execution(cron_expr, timezone):
    """计算下次执行时间"""
    try:
        # 解析cron表达式
        parts = cron_expr.split()
        if len(parts) != 5:
            return "无效的cron表达式"
        
        minute, hour, day, month, weekday = parts
        
        # 获取当前时间
        from datetime import datetime, timedelta
        import pytz
        
        # 设置时区
        try:
            tz = pytz.timezone(timezone)
            now = datetime.now(tz)
        except:
            now = datetime.now()
        
        # 简单的计算：假设是每日任务
        if day == '*' and month == '*' and weekday == '*':
            # 每日任务
            target_hour = int(hour) if hour != '*' else 0
            target_minute = int(minute) if minute != '*' else 0
            
            # 计算今天的目标时间
            target_time = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
            
            # 如果今天的时间已经过去，计算明天
            if target_time < now:
                target_time += timedelta(days=1)
            
            # 格式化输出
            return target_time.strftime('%Y-%m-%d %H:%M:%S')
        
        return "复杂cron表达式"
        
    except Exception as e:
        return f"计算失败: {str(e)}"

def create_default_schedule(task_type):
    """创建默认的定时设置"""
    if task_type == 'quick':
        return {
            'id': 0,
            'task_type': 'quick',
            'enabled': True,
            'schedule_expr': '30 18 * * *',
            'schedule_tz': 'Asia/Shanghai',
            'description': '每日快速检查网络设备状态',
            'last_updated': datetime.now().isoformat(),
            'next_execution': calculate_next_execution('30 18 * * *', 'Asia/Shanghai')
        }
    else:  # full
        return {
            'id': 0,
            'task_type': 'full',
            'enabled': False,
            'schedule_expr': '0 6 * * 0',
            'schedule_tz': 'Asia/Shanghai',
            'description': '每周日6:00完整扫描网络',
            'last_updated': datetime.now().isoformat(),
            'next_execution': calculate_next_execution('0 6 * * 0', 'Asia/Shanghai')
        }

def update_openclaw_cron_config(task_type, enabled, schedule_expr, schedule_tz):
    """更新OpenClaw cron配置（支持多个任务）"""
    try:
        cron_config_path = os.path.expanduser('~/.openclaw/cron/jobs.json')
        
        if not os.path.exists(cron_config_path):
            # 创建目录
            os.makedirs(os.path.dirname(cron_config_path), exist_ok=True)
            config = {
                'version': 1,
                'jobs': []
            }
        else:
            with open(cron_config_path, 'r') as f:
                config = json.load(f)
        
        # 根据任务类型设置任务ID和名称
        if task_type == 'quick':
            task_id_prefix = 'network_scan_quick'
            task_name = '网络快速扫描'
            task_description = '定时快速检查网络设备状态'
            payload_text = '执行网络快速扫描脚本: python scripts/network_scan.py --type quick'
        else:  # full
            task_id_prefix = 'network_scan_full'
            task_name = '网络完整扫描'
            task_description = '定时完整扫描网络发现新设备'
            payload_text = '执行网络完整扫描脚本: python scripts/network_scan.py --type full'
        
        # 查找对应类型的网络扫描任务
        task_index = None
        for i, job in enumerate(config.get('jobs', [])):
            if task_id_prefix in job.get('id', ''):
                task_index = i
                break
        
        if task_index is None:
            # 创建新任务
            new_task = {
                'id': task_id_prefix + '_' + datetime.now().strftime('%Y%m%d%H%M%S'),
                'name': task_name,
                'description': task_description,
                'enabled': enabled,
                'createdAtMs': int(datetime.now().timestamp() * 1000),
                'updatedAtMs': int(datetime.now().timestamp() * 1000),
                'schedule': {
                    'kind': 'cron',
                    'expr': schedule_expr,
                    'tz': schedule_tz
                },
                'sessionTarget': 'main',
                'wakeMode': 'now',
                'payload': {
                    'kind': 'systemEvent',
                    'text': payload_text
                },
                'state': {
                    'nextRunAtMs': 0,
                    'lastRunAtMs': 0,
                    'lastRunStatus': 'pending',
                    'lastStatus': 'pending',
                    'lastDurationMs': 0,
                    'lastDeliveryStatus': 'not-requested',
                    'consecutiveErrors': 0
                }
            }
            config['jobs'].append(new_task)
        else:
            # 更新现有任务
            config['jobs'][task_index]['enabled'] = enabled
            config['jobs'][task_index]['schedule']['expr'] = schedule_expr
            config['jobs'][task_index]['schedule']['tz'] = schedule_tz
            config['jobs'][task_index]['description'] = task_description
            config['jobs'][task_index]['payload']['text'] = payload_text
            config['jobs'][task_index]['updatedAtMs'] = int(datetime.now().timestamp() * 1000)
        
        # 保存配置
        with open(cron_config_path, 'w') as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        
        # 重启OpenClaw网关服务以应用更改
        try:
            import subprocess
            subprocess.run(['openclaw', 'gateway', 'restart'], capture_output=True, text=True)
        except:
            pass  # 忽略重启失败
        
        return True
        
    except Exception as e:
        print(f"更新OpenClaw cron配置失败: {e}")
        return False

# 应用启动时初始化（延迟执行）
def initialize_app():
    """应用初始化函数"""
    with app.app_context():
        init_database()
        scan_and_init_modules()
        print("✅ 应用初始化完成")

def scan_and_init_modules():
    """扫描并初始化代码模块数据"""
    conn = get_db_connection()
    
    # 定义核心模块
    core_modules = [
        {
            'name': 'app',
            'type': 'core',
            'path': 'app.py',
            'description': 'Flask主应用，包含所有API和页面路由'
        },
        {
            'name': 'network_scanner',
            'type': 'core',
            'path': 'network_scanner.py',
            'description': '内网设备扫描模块，支持快速/完整扫描'
        },
        {
            'name': 'data_stats_api',
            'type': 'api',
            'path': 'data_stats_api.py',
            'description': '数据统计API模块，提供token消耗分析'
        },
        {
            'name': 'openclaw_cron_integration',
            'type': 'integration',
            'path': 'openclaw_cron_integration.py',
            'description': 'OpenClaw定时任务集成模块'
        },
        {
            'name': 'token_tracker',
            'type': 'utility',
            'path': 'token_tracker.py',
            'description': 'Token消耗追踪工具'
        }
    ]
    
    # 定义脚本模块
    script_modules = [
        {
            'name': 'network_scan',
            'type': 'script',
            'path': 'scripts/network_scan.py',
            'description': '网络扫描定时任务脚本'
        },
        {
            'name': 'daily_review',
            'type': 'script',
            'path': 'scripts/daily_review.py',
            'description': '每日回顾脚本'
        },
        {
            'name': 'token_report',
            'type': 'script',
            'path': 'scripts/token_report.py',
            'description': 'Token报告生成脚本'
        },
        {
            'name': 'weekly_summary',
            'type': 'script',
            'path': 'scripts/weekly_summary.py',
            'description': '周度总结脚本'
        },
        {
            'name': 'cron_api',
            'type': 'api',
            'path': 'scripts/cron_api.py',
            'description': '定时任务API脚本'
        }
    ]
    
    all_modules = core_modules + script_modules
    
    for module in all_modules:
        # 检查模块是否已存在
        cursor = conn.execute(
            'SELECT id FROM code_modules WHERE module_name = ?',
            (module['name'],)
        )
        existing = cursor.fetchone()
        
        if existing:
            # 更新现有模块
            conn.execute('''
                UPDATE code_modules 
                SET module_type = ?, file_path = ?, description = ?, last_used = CURRENT_TIMESTAMP
                WHERE module_name = ?
            ''', (module['type'], module['path'], module['description'], module['name']))
        else:
            # 插入新模块
            # 估算代码行数和token节省
            import os
            file_path = os.path.join(os.path.dirname(__file__), module['path'])
            lines = 0
            if os.path.exists(file_path):
                with open(file_path, 'r') as f:
                    lines = len(f.readlines())
            
            # 估算token节省：每100行代码约节省500 tokens（避免重复开发）
            estimated_tokens = lines * 5 if lines > 0 else 100
            
            conn.execute('''
                INSERT INTO code_modules 
                (module_name, module_type, file_path, lines_of_code, description, estimated_tokens_saved)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (module['name'], module['type'], module['path'], lines, module['description'], estimated_tokens))
    
    conn.commit()
    
    # 添加一些示例使用记录
    import random
    from datetime import datetime, timedelta
    
    for module in all_modules:
        cursor = conn.execute(
            'SELECT id FROM code_modules WHERE module_name = ?',
            (module['name'],)
        )
        module_id = cursor.fetchone()['id']
        
        # 为每个模块添加一些使用记录
        for i in range(random.randint(1, 5)):
            days_ago = random.randint(0, 30)
            usage_time = datetime.now() - timedelta(days=days_ago, hours=random.randint(0, 23))
            
            # 估算每次使用节省的tokens
            tokens_saved = random.randint(50, 500)
            
            conn.execute('''
                INSERT INTO module_usage 
                (module_id, usage_context, tokens_estimated_saved, usage_timestamp)
                VALUES (?, ?, ?, ?)
            ''', (module_id, 'development', tokens_saved, usage_time.strftime('%Y-%m-%d %H:%M:%S')))
            
            # 更新模块的复用计数和总节省tokens
            conn.execute('''
                UPDATE code_modules 
                SET reuse_count = reuse_count + 1,
                    estimated_tokens_saved = estimated_tokens_saved + ?,
                    last_used = ?
                WHERE id = ?
            ''', (tokens_saved, usage_time.strftime('%Y-%m-%d %H:%M:%S'), module_id))
    
    conn.commit()
    conn.close()
    print(f"✅ 已初始化 {len(all_modules)} 个代码模块")


# ─── Pablo ↔ Pbee 对话记录 API ─────────────────────────────────

@app.route("/api/conversations", methods=["GET"])
def api_conversations_list():
    """获取对话记录列表"""
    conn = get_db_connection()
    direction = request.args.get("direction", "all")
    limit = request.args.get("limit", 200, type=int)
    if direction == "all":
        convs = conn.execute("SELECT * FROM agent_conversations ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    else:
        convs = conn.execute("SELECT * FROM agent_conversations WHERE direction=? ORDER BY id DESC LIMIT ?", (direction, limit)).fetchall()
    conn.close()
    return jsonify({"conversations": [dict(c) for c in convs], "total": len(convs)})


@app.route("/api/conversations", methods=["POST"])
def api_conversations_add():
    """添加对话记录"""
    data = request.get_json()
    if not data:
        return jsonify({"success": False, "error": "need json"}), 400
    conn = get_db_connection()
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor = conn.execute(
        "INSERT INTO agent_conversations (direction, sender, receiver, subject, content, msg_type, created_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (data.get("direction", "pbee_to_pablo"), data.get("sender", "Pbee"), data.get("receiver", "Pablo"),
         data.get("subject", ""), data.get("content", ""), data.get("type", "message"), now,
         data.get("updated_by", data.get("sender", "Pbee")))
    )
    conn.commit()
    conv_id = cursor.lastrowid
    conn.close()
    return jsonify({"success": True, "id": conv_id})


@app.route("/conversations")
def conversations_page():
    """聊天界面 - 通过KK Chat Server通信"""
    return render_template("chat.html")


@app.route("/api/kk/send", methods=["POST"])
def kk_send_api():
    """发消息给KK"""
    import urllib.request as _ur
    import json as _json
    data = request.get_json()
    if not data or not data.get("content"):
        return jsonify({"success": False, "error": "content 必填"}), 400
    content = data["content"].strip()
    if not content:
        return jsonify({"success": False, "error": "内容不能为空"}), 400
    # 直接发到KK的Chat Server (5003)
    try:
        req = _ur.Request(
            "http://localhost:5003/api/send",
            data=_json.dumps({"sender": "pablo", "content": content}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST")
        with _ur.urlopen(req, timeout=5) as resp:
            result = _json.loads(resp.read())
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            conn = get_db_connection()
            conn.execute(
                "INSERT INTO agent_conversations (direction, sender, receiver, content, msg_type, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                ("pablo_to_kk", "Pablo", "KK", content, "message", now))
            conn.commit()
            conn.close()
            # 直接relay到KK的3000（保底）
            try:
                relay_req = _ur.Request(
                    "http://192.168.1.83:3000/api/relay",
                    data=_json.dumps({"sender": "pablo", "content": content, "created_at": datetime.now().timestamp(), "token": "***"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST")
                _ur.urlopen(relay_req, timeout=3)
            except Exception:
                pass  # 保底失败不影响主流程
            return jsonify({"success": True, "id": result.get("id")})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/kk/messages")
def kk_messages_api():
    """获取消息记录"""
    import urllib.request as _ur
    import json as _json
    after = request.args.get("after", "0")
    try:
        with _ur.urlopen(f"http://localhost:5003/api/messages?after={after}", timeout=5) as resp:
            msgs = _json.loads(resp.read())
            return jsonify({"success": True, "messages": msgs})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500



# ─── 行情日报页面 (同步自本地) ───────────────────────────────

from datetime import datetime
from xiaoyu_routes import xiaoyu

STOCKS_CONFIG = {
    "US": [
        {"code": "NVDA",  "name": "英伟达"},
        {"code": "CRWV",  "name": "CoreWeave"},
        {"code": "MARA",  "name": "Marathon"},
        {"code": "CEG",   "name": "Constellation"},
        {"code": "BTDR",  "name": "Bitdeer"},
        {"code": "SMR",   "name": "NuScale"},
    ],
    "CN_A": [
        {"code": "688629", "name": "华丰科技"},
        {"code": "300811", "name": "铂科新材"},
        {"code": "002222", "name": "福晶科技"},
        {"code": "688981", "name": "中芯国际"},
        {"code": "002436", "name": "兴森科技"},
        {"code": "002384", "name": "东山精密"},
        {"code": "000880", "name": "潍柴重机"},
        {"code": "688802", "name": "沐曦"},
        {"code": "688795", "name": "摩尔线程"},
        {"code": "300260", "name": "新莱应材"},
    ],
    "CN_HK": [
        {"code": "9660.HK", "name": "地平线机器人"},
        {"code": "2432.HK", "name": "越疆机器人"},
        {"code": "3690.HK", "name": "美团"},
    ],
}

class ReportDatabase:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), "scores.db")
        self.db_path = db_path
        self._init_table()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_table(self):
        with self._get_connection() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS report_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, stock_code TEXT NOT NULL, stock_name TEXT NOT NULL, market TEXT NOT NULL, report_date TEXT NOT NULL, price REAL, change_pct REAL, currency TEXT DEFAULT 'USD', volume_signal TEXT, catalysts TEXT, news TEXT, analysis TEXT, prediction TEXT, target TEXT, catalyst_text TEXT, risk TEXT, confidence TEXT, raw_text TEXT, summary_top TEXT, summary_bottom TEXT, week_events TEXT, pred_short TEXT, pred_mid TEXT, pred_long TEXT, created_at TEXT NOT NULL, UNIQUE(stock_code, report_date))")
            for col in ["news", "analysis", "pred_short", "pred_mid", "pred_long"]:
                try:
                    conn.execute("ALTER TABLE report_entries ADD COLUMN " + col + " TEXT")
                    conn.commit()
                except: pass

    def upsert_entry(self, data):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        fields = ["stock_code", "stock_name", "market", "report_date", "price", "change_pct", "currency", "volume_signal", "catalysts", "news", "analysis", "prediction", "target", "catalyst_text", "risk", "confidence", "raw_text", "summary_top", "summary_bottom", "week_events", "pred_short", "pred_mid", "pred_long", "created_at"]
        values = [str(data.get(f, "") or "") for f in fields]
        values[fields.index("created_at")] = now
        with self._get_connection() as conn:
            placeholders = ", ".join(["?"] * len(fields))
            update_set = ", ".join([f + "=EXCLUDED." + f for f in fields[4:]])
            sql = "INSERT INTO report_entries (" + ", ".join(fields) + ") VALUES (" + placeholders + ") ON CONFLICT(stock_code, report_date) DO UPDATE SET " + update_set
            conn.execute(sql, values)
            conn.commit()
            c = conn.execute("SELECT id FROM report_entries WHERE stock_code=? AND report_date=?", (data["stock_code"], data["report_date"]))
            return c.fetchone()["id"]

    def get_entries(self, stock_code=None, market=None, start_date=None, end_date=None, limit=200):
        conds, params = [], []
        if stock_code: conds.append("stock_code=?"); params.append(stock_code)
        if market: conds.append("market=?"); params.append(market)
        if start_date: conds.append("report_date>=?"); params.append(start_date)
        if end_date: conds.append("report_date<=?"); params.append(end_date)
        where = " AND ".join(conds) if conds else "1=1"
        params.append(limit)
        with self._get_connection() as conn:
            c = conn.execute("SELECT * FROM report_entries WHERE " + where + " ORDER BY report_date DESC LIMIT ?", params)
            return [dict(row) for row in c.fetchall()]

    def get_dates(self, market=None):
        where = "WHERE market='" + market + "'" if market else ""
        with self._get_connection() as conn:
            c = conn.execute("SELECT DISTINCT report_date FROM report_entries " + where + " ORDER BY report_date DESC")
            return [row["report_date"] for row in c.fetchall()]

report_db = ReportDatabase()

@app.route("/reports")
def reports_page():
    """行情日报主页面（独立运行，不代理）"""
    stocks = STOCKS_CONFIG
    return render_template('reports.html', stocks=stocks)

def _old_reports_page():
    """代理到本地日报页面"""
    content, status, ctype = proxy_reports("/reports")
    return content, status

@app.route("/api/reports", methods=["GET"])
def get_reports():
    """直接返回本地日报数据"""
    stock_code = request.args.get("stock_code")
    market = request.args.get("market")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    limit = int(request.args.get("limit", 200))
    entries = report_db.get_entries(
        stock_code=stock_code, market=market,
        start_date=start_date, end_date=end_date, limit=limit
    )
    return jsonify({"success": True, "data": entries})

@app.route("/api/reports/stocks", methods=["GET"])
def get_report_stocks():
    return jsonify({"success": True, "data": STOCKS_CONFIG})

@app.route("/api/reports/dates", methods=["GET"])
def get_report_dates():
    return jsonify({"success": True, "data": report_db.get_dates(market=request.args.get("market"))})

@app.route("/api/reports", methods=["POST"])
def save_report():
    data = request.get_json()
    if not data or not data.get("stock_code") or not data.get("report_date"):
        return jsonify({"success": False, "error": "stock_code 和 report_date 必填"}), 400
    return jsonify({"success": True, "id": report_db.upsert_entry(data)})

@app.route("/api/reports/batch", methods=["POST"])
def save_reports_batch():
    entries = request.get_json()
    if not isinstance(entries, list):
        return jsonify({"success": False, "error": "需要数组"}), 400
    ids = [report_db.upsert_entry(d) for d in entries if d.get("stock_code") and d.get("report_date")]
    return jsonify({"success": True, "saved": len(ids), "ids": ids})




@app.route('/kk')
def kk_chat():
    import os as _os
    import html as _html
    msg_file = _os.path.expanduser('~/.openclaw/workspace/msg_from_pablo.md')
    content = ''
    if _os.path.exists(msg_file):
        with open(msg_file) as f:
            content = f.read()
    escaped = _html.escape(content)
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>KK ↔ Pablo 对话</title>
<style>
body { font-family: -apple-system, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; background: #f5f5f5; }
h1 { color: #333; border-bottom: 2px solid #4A90D9; padding-bottom: 10px; }
pre { background: white; border-radius: 8px; padding: 16px; white-space: pre-wrap; word-wrap: break-word; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
form { margin-top: 16px; }
textarea { width: 100%%; height: 100px; border: 1px solid #ddd; border-radius: 8px; padding: 12px; font-size: 14px; resize: vertical; }
button { background: #4A90D9; color: white; border: none; padding: 10px 24px; border-radius: 6px; cursor: pointer; font-size: 14px; }
button:hover { background: #357ABD; }
.msg { color: #666; margin-top: 8px; }
</style>
</head>
<body>
<h1>💬 KK ↔ Pablo 对话</h1>
<pre>""" + escaped + """</pre>
<hr>
<h3>发送消息给 KK</h3>
<form method="POST" action="/kk/send">
<textarea name="message" placeholder="输入你想对 KK 说的话..." required></textarea>
<br><br>
<button type="submit">发送 ➤</button>
</form>
</body>
</html>"""

@app.route('/kk/send', methods=['POST'])
def kk_send():
    from flask import request, redirect
    import os as _os
    from datetime import datetime
    msg = request.form.get('message', '').strip()
    if msg:
        msg_file = _os.path.expanduser('~/.openclaw/workspace/msg_from_pablo.md')
        now = datetime.now().strftime('%%Y-%%m-%%d %%H:%%M')
        entry = f"""
【{now} Pablo发】
{msg}

"""
        with open(msg_file, 'a') as f:
            f.write(entry)
    return redirect('/kk')


if __name__ == '__main__':
    # 应用初始化
    initialize_app()
    
    # 启动Flask应用
    print("=" * 60)
    print("Pablo 的智能演进看板")
    print("=" * 60)
    print(f"访问地址: http://localhost:5000")
    print(f"定时任务: http://localhost:5000/cron")
    print(f"API文档: http://localhost:5000/health")
    print(f"监控状态: http://localhost:5000/api/monitor/status")
    print(f"模块统计: http://localhost:5000/code-modules")
    print("=" * 60)
    
    # 允许局域网访问，关闭调试模式以避免自动重启
    
@app.route("/stocks")
def stocks():
    return render_template("stocks.html")

@app.route("/api/stock-pdfs")
def stock_pdfs():
    import os, glob
    pdf_dir = os.path.join(app.static_folder, "stock")
    os.makedirs(pdf_dir, exist_ok=True)
    files = []
    for f in sorted(glob.glob(os.path.join(pdf_dir, "*.pdf"))):
        files.append({
            "name": os.path.basename(f).replace(".pdf", ""),
            "url": "/static/stock/" + os.path.basename(f)
        })
    return jsonify({"files": files})

@app.route("/linkclaw")

def linkclaw():
    return '''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>LinkClaw - 与 KK 聊天</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { height: 100vh; overflow: hidden; background: #f0f0f0; }
  iframe { width: 100%; height: 100%; border: none; }
</style>
</head>
<body>
<iframe src="http://localhost:5003/?as=pablo"></iframe>
</body>
</html>'''





app.register_blueprint(xiaoyu)
app.run(host='0.0.0.0', port=5000, debug=False)
