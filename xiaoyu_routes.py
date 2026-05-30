"""
小渔学习成长系统 - Flask Blueprint 路由模块
复用 app.py 的 get_db_connection() 和 DB_PATH
"""

from flask import Blueprint, render_template, request, jsonify
import sqlite3
from datetime import datetime

xiaoyu = Blueprint('xiaoyu', __name__, url_prefix='/xiaoyu')


# ── 数据库工具 ──

def get_db():
    """获取本应用数据库连接（不导入 app.py 避免循环import）"""
    import os
    db_path = os.path.join(os.path.dirname(__file__), 'scores.db')
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables():
    """确保小渔系统所需的数据库表存在（自动建表）"""
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS xiaoyu_rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            score REAL NOT NULL DEFAULT 1.0,
            category TEXT DEFAULT '未分类',
            enabled INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xiaoyu_scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rule_id INTEGER,
            score REAL NOT NULL,
            reason TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xiaoyu_math_errors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            category TEXT DEFAULT '未分类',
            difficulty INTEGER DEFAULT 2,
            mastered INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            mastered_at TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS xiaoyu_checkin (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            check_date TEXT NOT NULL,
            check_type TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(check_date, check_type)
        );
    """)
    conn.commit()
    conn.close()


# ── 页面路由 ──

@xiaoyu.route('/')
def index():
    return render_template('xiaoyu.html')

@xiaoyu.route('/scoring')
def scoring():
    return render_template('xiaoyu_scoring.html')

@xiaoyu.route('/math')
def math():
    return render_template('xiaoyu_math.html')

@xiaoyu.route('/homework')
def homework():
    return render_template('xiaoyu_homework.html')

# ── 打分规则 API ──

@xiaoyu.route('/api/rules', methods=['GET'])
def list_rules():
    ensure_tables()
    conn = get_db()
    rows = conn.execute('SELECT * FROM xiaoyu_rules ORDER BY score DESC').fetchall()
    conn.close()
    return jsonify({'rules': [dict(r) for r in rows]})

@xiaoyu.route('/api/rules', methods=['POST'])
def add_rule():
    data = request.get_json()
    name = data.get('name', '').strip()
    score = data.get('score', 1)
    category = data.get('category', '未分类')
    if not name:
        return jsonify({'success': False, 'error': '名称不能为空'}), 400
    ensure_tables()
    conn = get_db()
    conn.execute('INSERT INTO xiaoyu_rules (name, score, category) VALUES (?, ?, ?)',
                 (name, score, category))
    conn.commit()
    rule_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
    conn.close()
    return jsonify({'success': True, 'id': rule_id})

@xiaoyu.route('/api/rules/<int:rule_id>', methods=['PUT'])
def update_rule(rule_id):
    """更新规则（复用 PUT 而非 DELETE，遵循 REST 风格）"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '无数据'}), 400
    ensure_tables()
    conn = get_db()
    updates = []
    params = []
    for field in ['name', 'score', 'category', 'enabled']:
        if field in data:
            updates.append(f'{field} = ?')
            params.append(data[field])
    if not updates:
        conn.close()
        return jsonify({'success': False, 'error': '没有需要更新的字段'}), 400
    params.append(rule_id)
    conn.execute(f'UPDATE xiaoyu_rules SET {", ".join(updates)} WHERE id = ?', params)
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@xiaoyu.route('/api/rules/<int:rule_id>', methods=['DELETE'])
def delete_rule(rule_id):
    ensure_tables()
    conn = get_db()
    conn.execute('DELETE FROM xiaoyu_rules WHERE id = ?', (rule_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@xiaoyu.route('/api/scores', methods=['GET'])
def list_scores():
    ensure_tables()
    conn = get_db()
    rows = conn.execute('''
        SELECT s.*, r.name as rule_name
        FROM xiaoyu_scores s
        LEFT JOIN xiaoyu_rules r ON s.rule_id = r.id
        ORDER BY s.created_at DESC
        LIMIT 50
    ''').fetchall()
    conn.close()
    return jsonify({'scores': [dict(r) for r in rows]})

@xiaoyu.route('/api/scores', methods=['POST'])
def add_score():
    data = request.get_json()
    rule_id = data.get('rule_id')
    reason = data.get('reason', '')
    period = data.get('period', '')
    period_category = data.get('period_category', '')
    ensure_tables()
    conn = get_db()
    rule = conn.execute('SELECT score, category FROM xiaoyu_rules WHERE id = ?', (rule_id,)).fetchone()
    if not rule:
        conn.close()
        return jsonify({'success': False, 'error': '规则不存在'}), 404
    # 如果没传 period_category，从规则继承
    if not period_category:
        period_category = rule['category'] or ''
    conn.execute('INSERT INTO xiaoyu_scores (rule_id, score, reason, period, period_category) VALUES (?, ?, ?, ?, ?)',
                 (rule_id, rule['score'], reason, period, period_category))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@xiaoyu.route('/api/scores/<int:score_id>', methods=['DELETE'])
def delete_score(score_id):
    """删除一条评分记录"""
    ensure_tables()
    conn = get_db()
    cur = conn.execute('DELETE FROM xiaoyu_scores WHERE id = ?', (score_id,))
    conn.commit()
    deleted = cur.rowcount
    conn.close()
    if deleted == 0:
        return jsonify({'success': False, 'error': '记录不存在'}), 404
    return jsonify({'success': True})

@xiaoyu.route('/api/scores/<int:score_id>', methods=['PUT'])
def update_score(score_id):
    """编辑一条评分记录（修改 reason 或 rule_id）"""
    data = request.get_json()
    reason = data.get('reason')
    rule_id = data.get('rule_id')
    ensure_tables()
    conn = get_db()
    if reason is not None:
        conn.execute('UPDATE xiaoyu_scores SET reason = ? WHERE id = ?', (reason, score_id))
    if rule_id is not None:
        rule = conn.execute('SELECT score FROM xiaoyu_rules WHERE id = ?', (rule_id,)).fetchone()
        if not rule:
            conn.close()
            return jsonify({'success': False, 'error': '规则不存在'}), 404
        conn.execute('UPDATE xiaoyu_scores SET rule_id = ?, score = ? WHERE id = ?',
                     (rule_id, rule['score'], score_id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@xiaoyu.route('/api/goals', methods=['GET', 'POST'])
def goals_api():
    ensure_tables()
    conn = get_db()
    # 建表
    conn.execute('''
        CREATE TABLE IF NOT EXISTS xiaoyu_goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_score REAL NOT NULL DEFAULT 100,
            reward TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    
    if request.method == 'GET':
        goal = conn.execute('SELECT * FROM xiaoyu_goals ORDER BY id DESC LIMIT 1').fetchone()
        conn.close()
        if goal:
            return jsonify({'success': True, 'goal': dict(goal)})
        return jsonify({'success': True, 'goal': None})
    
    # POST
    data = request.get_json()
    target_score = data.get('target_score', 100)
    reward = data.get('reward', '')
    cursor = conn.execute('INSERT INTO xiaoyu_goals (target_score, reward) VALUES (?, ?)',
                 (target_score, reward))
    conn.commit()
    goal_id = cursor.lastrowid
    goal = conn.execute('SELECT * FROM xiaoyu_goals WHERE id = ?', (goal_id,)).fetchone()
    conn.close()
    return jsonify({'success': True, 'goal': dict(goal)})

@xiaoyu.route('/api/scores/stats', methods=['GET'])
def score_stats():
    """评分统计：今日、本周、本月"""
    ensure_tables()
    conn = get_db()
    today = datetime.now().strftime('%Y-%m-%d')
    today_dt = datetime.now()
    week_start = (today_dt - __import__('datetime').timedelta(days=today_dt.weekday())).strftime('%Y-%m-%d')
    month = datetime.now().strftime('%Y-%m')
    
    today_total = conn.execute(
        "SELECT COALESCE(SUM(score), 0) FROM xiaoyu_scores WHERE created_at >= ?",
        (today,)
    ).fetchone()[0]
    

    week_total = conn.execute(
        "SELECT COALESCE(SUM(score), 0) FROM xiaoyu_scores WHERE created_at >= ?",
        (week_start,)
    ).fetchone()[0]

    month_total = conn.execute(
        "SELECT COALESCE(SUM(score), 0) FROM xiaoyu_scores WHERE created_at LIKE ?",
        (f'{month}%',)
    ).fetchone()[0]

    all_total = conn.execute(
        "SELECT COALESCE(SUM(score), 0) FROM xiaoyu_scores"
    ).fetchone()[0]
    
    rule_counts = conn.execute('''
        SELECT r.name, COUNT(*) as cnt, SUM(s.score) as total
        FROM xiaoyu_scores s
        JOIN xiaoyu_rules r ON s.rule_id = r.id
        GROUP BY s.rule_id
        ORDER BY total DESC
        LIMIT 10
    ''').fetchall()
    
    conn.close()
    return jsonify({
        'today_total': today_total,
        'week_total': week_total,
        'month_total': month_total,
        'all_total': all_total,
        'top_rules': [dict(r) for r in rule_counts]
    })


# ── 数学错题 API ──

@xiaoyu.route('/api/math-errors', methods=['GET'])
def list_math_errors():
    ensure_tables()
    conn = get_db()
    rows = conn.execute(
        'SELECT * FROM xiaoyu_math_errors ORDER BY mastered ASC, created_at DESC'
    ).fetchall()
    conn.close()
    return jsonify({'errors': [dict(r) for r in rows]})

@xiaoyu.route('/api/math-errors', methods=['POST'])
def add_math_error():
    data = request.get_json()
    title = data.get('title', '').strip()
    category = data.get('category', '未分类')
    difficulty = data.get('difficulty', 2)
    if not title:
        return jsonify({'success': False, 'error': '题目不能为空'}), 400
    ensure_tables()
    conn = get_db()
    conn.execute(
        'INSERT INTO xiaoyu_math_errors (title, category, difficulty) VALUES (?, ?, ?)',
        (title, category, difficulty)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@xiaoyu.route('/api/math-errors/<int:error_id>', methods=['PUT'])
def update_math_error(error_id):
    """更新错题信息"""
    data = request.get_json()
    if not data:
        return jsonify({'success': False, 'error': '无数据'}), 400
    ensure_tables()
    conn = get_db()
    updates = []
    params = []
    for field in ['title', 'category', 'difficulty']:
        if field in data:
            updates.append(f'{field} = ?')
            params.append(data[field])
    if not updates:
        conn.close()
        return jsonify({'success': False, 'error': '无更新字段'}), 400
    params.append(error_id)
    conn.execute(
        f'UPDATE xiaoyu_math_errors SET {", ".join(updates)} WHERE id = ?', params
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@xiaoyu.route('/api/math-errors/<int:error_id>', methods=['DELETE'])
def delete_math_error(error_id):
    ensure_tables()
    conn = get_db()
    conn.execute('DELETE FROM xiaoyu_math_errors WHERE id = ?', (error_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@xiaoyu.route('/api/math-errors/<int:error_id>/toggle', methods=['POST'])
def toggle_math_error(error_id):
    """切换错题的「已掌握」状态"""
    ensure_tables()
    conn = get_db()
    err = conn.execute(
        'SELECT mastered FROM xiaoyu_math_errors WHERE id = ?', (error_id,)
    ).fetchone()
    if not err:
        conn.close()
        return jsonify({'success': False, 'error': '记录不存在'}), 404
    new_mastered = 0 if err['mastered'] else 1
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S') if new_mastered else None
    conn.execute(
        'UPDATE xiaoyu_math_errors SET mastered = ?, mastered_at = ? WHERE id = ?',
        (new_mastered, now, error_id)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'mastered': new_mastered})
# 以下是添加到 xiaoyu_routes.py 的内容
# 插入位置: 在 ensure_tables() 函数的 SQL 建表语句末尾
# 
# 在 xiaoyu_rules 建表语句之后，追加:

        

# ── 打卡 API ──
@xiaoyu.route('/api/checkin_today', methods=['GET'])
def get_today_checkin():
    """获取今日打卡状态"""
    from datetime import date
    d = date.today().isoformat()
    ensure_tables()
    conn = get_db()
    rows = conn.execute(
        'SELECT check_type FROM xiaoyu_checkin WHERE check_date = ?', (d,)
    ).fetchall()
    conn.close()
    result = {r['check_type']: True for r in rows}
    return jsonify({'date': d, 'checkin': result})


@xiaoyu.route('/api/checkin', methods=['POST'])
def do_checkin():
    """打卡：美梯英语/学校英语/口算"""
    data = request.get_json()
    check_date = data.get('date', '')
    check_type = data.get('type', '')
    toggle = data.get('toggle', False)
    if check_type not in ('美梯英语', '学校英语', '口算'):
        return jsonify({'success': False, 'error': '无效的打卡类型'}), 400
    from datetime import date
    if not check_date:
        check_date = date.today().isoformat()
    ensure_tables()
    conn = get_db()
    existing = conn.execute(
        'SELECT id FROM xiaoyu_checkin WHERE check_date = ? AND check_type = ?',
        (check_date, check_type)
    ).fetchone()
    if existing:
        if toggle:
            conn.execute('DELETE FROM xiaoyu_checkin WHERE id = ?', (existing['id'],))
            conn.commit()
            conn.close()
            return jsonify({'success': True, 'checked': False, 'action': 'removed'})
        conn.close()
        return jsonify({'success': True, 'checked': True, 'action': 'already_exists'})
    conn.execute(
        'INSERT INTO xiaoyu_checkin (check_date, check_type) VALUES (?, ?)',
        (check_date, check_type)
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'checked': True, 'action': 'created'})


@xiaoyu.route('/api/checkin/<string:check_date>', methods=['GET'])
def get_checkin(check_date):
    """获取指定日期的打卡状态"""
    ensure_tables()
    conn = get_db()
    rows = conn.execute(
        'SELECT check_type, created_at FROM xiaoyu_checkin WHERE check_date = ?',
        (check_date,)
    ).fetchall()
    conn.close()
    result = {r['check_type']: True for r in rows}
    return jsonify({'date': check_date, 'checkin': result})


@xiaoyu.route('/api/checkin_month/<int:year>/<int:month>', methods=['GET'])
def get_month_checkin(year, month):
    """获取某月的打卡状态"""
    ensure_tables()
    conn = get_db()
    prefix = f"{year:04d}-{month:02d}"
    rows = conn.execute(
        'SELECT check_date, check_type FROM xiaoyu_checkin WHERE check_date LIKE ?',
        (prefix + '%',)
    ).fetchall()
    conn.close()
    result = {}
    for r in rows:
        d = r['check_date']
        if d not in result:
            result[d] = {}
        result[d][r['check_type']] = True
    return jsonify({'year': year, 'month': month, 'data': result})


@xiaoyu.route('/api/checkin/streaks', methods=['GET'])
def get_checkin_streaks():
    """获取每种打卡的连续天数统计"""
    from datetime import date, timedelta
    ensure_tables()
    conn = get_db()
    today = date.today()
    result = {}
    for check_type in ('美梯英语', '学校英语', '口算'):
        rows = conn.execute(
            'SELECT check_date FROM xiaoyu_checkin WHERE check_type = ? ORDER BY check_date DESC',
            (check_type,)
        ).fetchall()
        dates = sorted(set(r['check_date'] for r in rows), reverse=True)
        streak = 0
        d = today
        while d.isoformat() in dates:
            streak += 1
            d -= timedelta(days=1)
        monday = today - timedelta(days=today.weekday())
        week_checkin = len([d for d in dates if d >= monday.isoformat()])
        days_passed = min(7, today.weekday() + 1)
        week_miss = max(0, days_passed - week_checkin)
        result[check_type] = {
            'current_streak': streak,
            'week_checkin': week_checkin,
            'week_miss': week_miss
        }
    conn.close()
    return jsonify(result)


@xiaoyu.route('/api/checkin/auto-score', methods=['POST'])
def auto_checkin_score():
    """
    按完整一周（周一到周日）的打卡记录生成打分。
    只对口算和学校英语生成。
    """
    from datetime import date, timedelta
    ensure_tables()
    conn = get_db()
    today = date.today()
    scores_created = []

    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    for check_type in ('学校英语', '口算'):
        rows = conn.execute(
            'SELECT check_date FROM xiaoyu_checkin WHERE check_type = ? AND check_date >= ? AND check_date <= ? ORDER BY check_date',
            (check_type, monday.isoformat(), sunday.isoformat())
        ).fetchall()
        check_dates = set(r['check_date'] for r in rows)
        week_checkin = len(check_dates)
        week_miss = 7 - week_checkin

        miss_to_score = {0: 10, 1: 5, 2: 3, 3: 1, 4: -1, 5: -5, 6: -7, 7: -10}
        category_map = {'学校英语': '坚持英语打卡', '口算': '坚持口算'}
        cat = category_map[check_type]

        matched_score = miss_to_score.get(week_miss, None)
        if matched_score is not None:
            rule = conn.execute(
                'SELECT id, name FROM xiaoyu_rules WHERE category = ? AND score = ? LIMIT 1',
                (cat, matched_score)
            ).fetchone()
            if rule:
                rule_id = rule['id']
                rname = rule['name']
                existing = conn.execute(
                    'SELECT id FROM xiaoyu_scores WHERE rule_id = ? AND created_at >= ?',
                    (rule_id, today.isoformat())
                ).fetchone()
                if not existing:
                    reason = f"本周{check_type}打卡{week_checkin}天，缺{week_miss}天"
                    conn.execute(
                        'INSERT INTO xiaoyu_scores (rule_id, score, reason) VALUES (?, ?, ?)',
                        (rule_id, matched_score, reason)
                    )
                    scores_created.append({
                        'rule': rname,
                        'score': matched_score,
                        'week_checkin': week_checkin,
                        'week_miss': week_miss
                    })

    conn.commit()
    conn.close()
    return jsonify({'success': True, 'scores_created': scores_created})
@xiaoyu.route('/api/checkin/sync-from-scores', methods=['POST'])
def sync_checkin_from_scores():
    """从打分记录反推打卡状态，更新日历"""
    from datetime import date, timedelta
    ensure_tables()
    conn = get_db()
    
    # 1. 扫描所有带"今日"关键词的打分记录
    scores = conn.execute(
        "SELECT id, created_at, score, reason FROM xiaoyu_scores WHERE reason LIKE '%今日%'"
    ).fetchall()
    
    new_checkins = 0
    for s in scores:
        reason = s['reason']
        score_date = s['created_at'][:10]  # '2026-05-30'
        
        # 从 reason 判断打卡类型
        check_type = None
        if '美梯英语' in reason or '美梯' in reason:
            check_type = '美梯英语'
        elif '学校英语' in reason:
            check_type = '学校英语'
        elif '口算' in reason:
            check_type = '口算'
        
        if not check_type:
            continue
        
        # 插入到打卡表（忽略已存在的，用 IGNORE 或 OR REPLACE）
        try:
            conn.execute(
                "INSERT OR IGNORE INTO xiaoyu_checkin (check_date, check_type) VALUES (?, ?)",
                (score_date, check_type)
            )
            if conn.total_changes > 0:
                new_checkins += 1
        except Exception as e:
            pass  # UNIQUE约束处理
    
    # 2. 也扫描旧格式的 reason（"5月28日，连续满5天"），尝试推断
    old_scores = conn.execute(
        "SELECT id, created_at, score, reason FROM xiaoyu_scores WHERE reason NOT LIKE '%今日%'"
    ).fetchall()
    
    for s in old_scores:
        reason = s['reason']
        score_date = s['created_at'][:10]
        
        # 旧格式 reason 可能包含打卡关键词
        check_type = None
        if '口算' in reason:
            check_type = '口算'
        elif '美梯' in reason:
            check_type = '美梯英语'
        elif '英语' in reason or ('打卡' in reason and '英语' not in reason):
            check_type = '学校英语'
        
        if not check_type:
            continue
        
        try:
            conn.execute(
                "INSERT OR IGNORE INTO xiaoyu_checkin (check_date, check_type) VALUES (?, ?)",
                (score_date, check_type)
            )
            if conn.total_changes > 0:
                new_checkins += 1
        except Exception:
            pass
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'synced': new_checkins})


@xiaoyu.route('/api/checkin_tags', methods=['GET'])
def get_checkin_tags():
    conn = get_db()
    conn.execute('CREATE TABLE IF NOT EXISTS xiaoyu_config (key TEXT PRIMARY KEY, value TEXT)')
    cur = conn.execute('SELECT value FROM xiaoyu_config WHERE key = ?', ('checkin_tags',))
    row = cur.fetchone()
    conn.close()
    if row:
        import json
        return jsonify({'tags': json.loads(row[0])})
    return jsonify({'tags': ['美梯英语', '学校英语', '口算']})

@xiaoyu.route('/api/checkin_tags', methods=['POST'])
def set_checkin_tags():
    import json
    data = request.get_json()
    tags = data.get('tags', [])
    conn = get_db()
    conn.execute('CREATE TABLE IF NOT EXISTS xiaoyu_config (key TEXT PRIMARY KEY, value TEXT)')
    conn.execute(
        'INSERT OR REPLACE INTO xiaoyu_config (key, value) VALUES (?, ?)',
        ('checkin_tags', json.dumps(tags, ensure_ascii=False))
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True})
