#!/usr/bin/env python3
"""
Wiki — 轻量级 Markdown Wiki 系统
独立 Flask 应用，通过反向代理挂载到主站 /wiki 路径下

特性：
- Markdown 编辑/预览
- 目录树（页面 + 子页面）
- 用户登录（用户名/密码）
- 页面级权限（可读、可写、管理员）
- 粘贴图片自动上传
- SQLite 存储
"""

import os
import sys
import sqlite3
import hashlib
import uuid
import re
import base64
import datetime
import mimetypes

from flask import (
    Flask, render_template, request, jsonify, redirect,
    url_for, session, abort, send_from_directory, send_file
)
from functools import wraps
from pathlib import Path

# ===== 配置 =====
# 修改这些值来配置你的 Wiki
WIKI_TITLE = "Private Wiki"           # Wiki 名称
SECRET_KEY = "change-this-to-random-key"  # 会话密钥（请修改！）
PORT = 5003                            # 监听端口
HOST = "0.0.0.0"                       # 监听地址
MAX_UPLOAD_SIZE = 16 * 1024 * 1024     # 最大上传 16MB
APPLICATION_ROOT = "/wiki"             # URL 路径前缀（设为 "/" 则直接根路径访问）

WIKI_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = WIKI_DIR / "wiki.db"
UPLOAD_DIR = WIKI_DIR / "uploads"
UPLOAD_DIR.mkdir(exist_ok=True)

# ===== Flask 应用 =====
app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["APPLICATION_ROOT"] = APPLICATION_ROOT
app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_SIZE

# ===== 数据库初始化 =====
def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_db()
    c = conn.cursor()
    
    # 用户表
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            display_name TEXT DEFAULT '',
            is_admin INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    
    # 页面表
    c.execute("""
        CREATE TABLE IF NOT EXISTS pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            parent_id INTEGER DEFAULT NULL,
            content TEXT DEFAULT '',
            content_html TEXT DEFAULT '',
            sort_order INTEGER DEFAULT 0,
            is_published INTEGER DEFAULT 1,
            created_by INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (parent_id) REFERENCES pages(id) ON DELETE SET NULL,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)
    
    # 页面权限表
    c.execute("""
        CREATE TABLE IF NOT EXISTS page_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            user_id INTEGER,
            permission TEXT NOT NULL CHECK(permission IN ('read', 'write', 'admin')),
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
    """)
    
    # 页面历史表
    c.execute("""
        CREATE TABLE IF NOT EXISTS page_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            page_id INTEGER NOT NULL,
            content TEXT,
            content_html TEXT,
            edited_by INTEGER,
            version INTEGER,
            edited_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            summary TEXT DEFAULT '',
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE,
            FOREIGN KEY (edited_by) REFERENCES users(id)
        )
    """)
    
    # 上传文件表
    c.execute("""
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            original_name TEXT NOT NULL,
            filepath TEXT NOT NULL,
            mime_type TEXT DEFAULT '',
            size INTEGER DEFAULT 0,
            uploaded_by INTEGER,
            page_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (uploaded_by) REFERENCES users(id),
            FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE SET NULL
        )
    """)
    
    # 创建默认管理员（仅首次运行，密码从环境变量读取或自动生成）
    admin_pw = os.environ.get('WIKI_ADMIN_PASSWORD') or 'admin123'
    default_pw = hashlib.sha256(admin_pw.encode()).hexdigest()
    c.execute("""
        INSERT OR IGNORE INTO users (username, password_hash, display_name, is_admin)
        VALUES (?, ?, ?, ?)
    """, ("admin", default_pw, "管理员", 1))
    
    # 创建首页
    c.execute("SELECT id FROM pages WHERE slug = 'home'")
    if not c.fetchone():
        c.execute("""
            INSERT INTO pages (title, slug, content, content_html, created_by)
            VALUES (?, ?, ?, ?, ?)
        """, ("首页", "home", "# 欢迎使用 Wiki\n\n点击左上角「新建页面」开始创建内容。", 
               "<h1>欢迎使用 Wiki</h1><p>点击左上角「新建页面」开始创建内容。</p>", 1))
    
    conn.commit()
    conn.close()

# ===== Markdown 转 HTML（简易版） =====
def md_to_html(md_text):
    """将 Markdown 文本转换为 HTML（不支持代码块的简化版本）"""
    if not md_text:
        return ""
    
    html = md_text
    
    # 代码块（``` ... ```）
    html = re.sub(r'```(\w*)\n(.*?)```', r'<pre><code>\2</code></pre>', html, flags=re.DOTALL)
    
    # 行内代码
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)
    
    # 标题
    html = re.sub(r'^### (.+)$', r'<h3>\1</h3>', html, flags=re.MULTILINE)
    html = re.sub(r'^## (.+)$', r'<h2>\1</h2>', html, flags=re.MULTILINE)
    html = re.sub(r'^# (.+)$', r'<h1>\1</h1>', html, flags=re.MULTILINE)
    
    # 粗体和斜体
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'\*(.+?)\*', r'<em>\1</em>', html)
    
    # 图片 ![](url)
    html = re.sub(r'!\[([^\]]*)\]\(([^)]+)\)', r'<img src="\2" alt="\1">', html)
    
    # 链接 [text](url)
    html = re.sub(r'\[([^\]]+)\]\(([^)]+)\)', r'<a href="\2">\1</a>', html)
    
    # 无序列表
    html = re.sub(r'^(\s*)[-*] (.+)$', r'\1<li>\2</li>', html, flags=re.MULTILINE)
    html = re.sub(r'(<li>.*</li>\n)+', r'<ul>\n\g<0></ul>\n', html, flags=re.DOTALL)
    
    # 有序列表
    html = re.sub(r'^(\s*)\d+\. (.+)$', r'\1<li>\2</li>', html, flags=re.MULTILINE)
    html = re.sub(r'(<li>.*</li>\n?)+', r'<ol>\n\g<0></ol>\n', html, flags=re.DOTALL)
    
    # 表格 | col1 | col2 |
    # 检测表格行（| 开头和结尾）
    table_rows = re.findall(r'^\|(.+)\|\s*$', html, re.MULTILINE)
    if len(table_rows) >= 2:
        def replace_table(m):
            block = m.group(0)
            lines = [l.strip() for l in block.split('\n') if l.strip()]
            # 分离表头、分隔行、数据行
            theader = lines[0]
            sep = lines[1] if len(lines) > 1 and '---' in lines[1] else None
            data_lines = lines[2:] if sep else lines[1:]
            cells = [c.strip() for c in theader.strip('|').split('|')]
            trs = '<tr>' + ''.join(f'<th>{c}</th>' for c in cells) + '</tr>\n'
            for dl in data_lines:
                cells = [c.strip() for c in dl.strip('|').split('|')]
                trs += '<tr>' + ''.join(f'<td>{c}</td>' for c in cells) + '</tr>\n'
            return '<table>\n' + trs + '</table>'
        # 匹配整个表格块
        table_block = r'(?:^\|.+\|\s*$\n?){2,}'
        html = re.sub(table_block, replace_table, html, flags=re.MULTILINE)
    
    # 引用
    html = re.sub(r'^> (.+)$', r'<blockquote>\1</blockquote>', html, flags=re.MULTILINE)
    
    # 水平线
    html = re.sub(r'^---+\s*$', r'<hr>', html, flags=re.MULTILINE)
    
    # 段落（两个换行）
    paragraphs = re.split(r'\n\n+', html)
    processed = []
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if not p.startswith('<h') and not p.startswith('<ul') and not p.startswith('<ol') and \
           not p.startswith('<li') and not p.startswith('<blockquote') and not p.startswith('<pre') and \
           not p.startswith('<hr') and not p.startswith('<img') and not p.startswith('<svg') and \
           not p.startswith('<table') and not p.startswith('<div') and not p.startswith('<p'):
            p = f'<p>{p}</p>'
        processed.append(p)
    
    html = '\n'.join(processed)
    
    # 换行（仅对非 HTML 块级元素内的内容）
    # 不再全局替换 \n 为 <br>，因为会破坏 <svg>、<table> 等
    html = html.replace('\n', '<br>')
    
    return html

# ===== 权限装饰器 =====
def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            if request.is_json:
                return jsonify({"error": "未登录"}), 401
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session or session.get('is_admin') != 1:
            abort(403)
        return f(*args, **kwargs)
    return decorated

def get_user_permission(page_id, user_id):
    """获取用户对某个页面的权限"""
    conn = get_db()
    c = conn.cursor()
    
    # 管理员拥有所有权限
    c.execute("SELECT is_admin FROM users WHERE id = ?", (user_id,))
    user = c.fetchone()
    if user and user['is_admin'] == 1:
        conn.close()
        return 'admin'
    
    # 检查显式权限
    c.execute("""
        SELECT permission FROM page_permissions 
        WHERE page_id = ? AND user_id = ?
    """, (page_id, user_id))
    perm = c.fetchone()
    conn.close()
    
    return perm['permission'] if perm else None

def can_read_page(page_id, user_id):
    """是否有读取权限"""
    perm = get_user_permission(page_id, user_id)
    return perm in ('read', 'write', 'admin') or perm is True

def can_write_page(page_id, user_id):
    """是否有写入权限"""
    perm = get_user_permission(page_id, user_id)
    return perm in ('write', 'admin')

# ===== 路由：认证 =====
@app.route("/")
@app.route("/index")
def root_redirect():
    return redirect(url_for("index"))
@app.route('/wiki/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '')
        password = request.form.get('password', '')
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        
        conn = get_db()
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE username = ? AND password_hash = ?", 
                  (username, password_hash))
        user = c.fetchone()
        conn.close()
        
        if user:
            session['user_id'] = user['id']
            session['username'] = user['username']
            session['display_name'] = user['display_name'] or user['username']
            session['is_admin'] = user['is_admin']
            return redirect(url_for('index'))
        
        return render_template('login.html', error='用户名或密码错误')
    
    return render_template('login.html')

@app.route('/wiki/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/wiki/change-password', methods=['POST'])
@login_required
def change_password():
    # 只从 form 读取（前端改用 form 提交）
    old_pw = request.form.get('old_password', '')
    new_pw = request.form.get('new_password', '')
    
    if not new_pw or len(new_pw) < 6:
        return jsonify({"error": "新密码至少6位"}), 400
    
    old_hash = hashlib.sha256(old_pw.encode()).hexdigest()
    new_hash = hashlib.sha256(new_pw.encode()).hexdigest()
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM users WHERE id = ? AND password_hash = ?",
              (session['user_id'], old_hash))
    if not c.fetchone():
        conn.close()
        return jsonify({"error": "原密码错误"}), 400
    
    c.execute("UPDATE users SET password_hash = ? WHERE id = ?",
              (new_hash, session['user_id']))
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "密码已修改"})

# ===== 路由：页面 =====
@app.route('/wiki/', methods=['GET', 'POST'])
@app.route('/wiki', methods=['GET', 'POST'])
@app.route('/wiki/<slug>', methods=['GET', 'POST'])
@login_required
def index(slug=None):
    page_slug = slug or request.args.get('page', 'home')

    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT * FROM pages WHERE slug = ?", (page_slug,))
    page = c.fetchone()
    
    # 获取目录树
    c.execute("SELECT id, title, slug, parent_id, sort_order FROM pages WHERE is_published = 1 ORDER BY sort_order, title")
    all_pages = c.fetchall()
    
    # 构建树
    tree = build_tree(all_pages)
    
    # 获取用户列表（用于权限管理）
    c.execute("SELECT id, username, display_name FROM users ORDER BY username")
    users = c.fetchall()
    
    conn.close()
    
    import json
    html = render_template('wiki.html', 
                         page=page, 
                         tree=tree,
                         users=users,
                         users_json=json.dumps([dict(u) for u in users], ensure_ascii=False),
                         page_slug=page_slug,
                         current_user_id=session.get('user_id', 0))
    # 确保返回字符串（Flask 3.x dev server 兼容性）
    if isinstance(html, str):
        return html
    return html.get_data(as_text=True) if hasattr(html, 'get_data') else str(html)


@app.route('/wiki/public/<slug>')
def public_page(slug):
    """公开页面，不需要登录"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM pages WHERE slug = ?", (slug,))
    page = c.fetchone()
    if not page:
        conn.close()
        return '<h1>页面不存在</h1>', 404
    c.execute("SELECT id, title, slug, parent_id, sort_order FROM pages WHERE is_published = 1 ORDER BY sort_order, title")
    pages = c.fetchall()
    conn.close()
    all_pages = build_tree(pages)
    return render_template('wiki.html',
                         page=page,
                         pages=all_pages,
                         page_slug=slug,
                         current_user_id=0,
                         users_json='[]')
    page_slug = slug or request.args.get('page', 'home')

    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT * FROM pages WHERE slug = ?", (page_slug,))
    page = c.fetchone()
    
    # 获取目录树
    c.execute("SELECT id, title, slug, parent_id, sort_order FROM pages WHERE is_published = 1 ORDER BY sort_order, title")
    all_pages = c.fetchall()
    
    # 构建树
    tree = build_tree(all_pages)
    
    # 获取用户列表（用于权限管理）
    c.execute("SELECT id, username, display_name FROM users ORDER BY username")
    users = c.fetchall()
    
    conn.close()
    
    import json
    html = render_template('wiki.html', 
                         page=page, 
                         tree=tree,
                         users=users,
                         users_json=json.dumps([dict(u) for u in users], ensure_ascii=False),
                         page_slug=page_slug,
                         current_user_id=session.get('user_id', 0))
    # 确保返回字符串（Flask 3.x dev server 兼容性）
    if isinstance(html, str):
        return html
    return html.get_data(as_text=True) if hasattr(html, 'get_data') else str(html)

def build_tree(pages, parent_id=None):
    """构建目录树"""
    tree = []
    for p in pages:
        if p['parent_id'] == parent_id:
            children = build_tree(pages, p['id'])
            node = {
                'id': p['id'],
                'title': p['title'],
                'slug': p['slug'],
                'sort_order': p['sort_order'],
                'children': children
            }
            tree.append(node)
    return tree

@app.route('/wiki/api/pages', methods=['GET'])
def get_all_pages():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, title, slug, parent_id, sort_order, created_at, updated_at FROM pages WHERE is_published = 1 ORDER BY sort_order, title")
    pages = c.fetchall()
    conn.close()
    return jsonify([dict(p) for p in pages])

@app.route('/wiki/api/pages/<slug>/public', methods=['GET'])
def get_public_page(slug):
    """公开页面 API，不需要登录"""
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM pages WHERE slug = ?", (slug,))
    page = c.fetchone()
    conn.close()
    if not page:
        return jsonify({'error': '页面不存在'}), 404
    return jsonify(dict(page))

@app.route('/wiki/api/pages/<slug>', methods=['GET'])
@login_required
def get_page(slug):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM pages WHERE slug = ?", (slug,))
    page = c.fetchone()
    conn.close()
    
    if not page:
        return jsonify({"error": "页面不存在"}), 404
    
    # 获取权限
    allowed = {}
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT pp.permission, u.username, u.display_name, u.id as user_id
        FROM page_permissions pp
        JOIN users u ON pp.user_id = u.id
        WHERE pp.page_id = ?
    """, (page['id'],))
    for perm in c.fetchall():
        allowed[perm['username']] = {
            'permission': perm['permission'],
            'display_name': perm['display_name'],
            'user_id': perm['user_id']
        }
    conn.close()
    
    result = dict(page)
    result['permissions'] = allowed
    return jsonify(result)

@app.route('/wiki/api/pages/<slug>/history', methods=['GET'])
@login_required
def get_page_history(slug):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM pages WHERE slug = ?", (slug,))
    page = c.fetchone()
    if not page:
        return jsonify({"error": "页面不存在"}), 404
    
    c.execute("""
        SELECT ph.version, ph.summary, ph.edited_at, u.username, u.display_name
        FROM page_history ph
        LEFT JOIN users u ON ph.edited_by = u.id
        WHERE ph.page_id = ?
        ORDER BY ph.version DESC LIMIT 50
    """, (page['id'],))
    history = c.fetchall()
    conn.close()
    
    return jsonify([dict(h) for h in history])

@app.route('/wiki/api/pages/<slug>/history/<int:version>', methods=['GET'])
@login_required
def get_page_version(slug, version):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM pages WHERE slug = ?", (slug,))
    page = c.fetchone()
    if not page:
        return jsonify({"error": "页面不存在"}), 404
    
    c.execute("SELECT * FROM page_history WHERE page_id = ? AND version = ?",
              (page['id'], version))
    hist = c.fetchone()
    conn.close()
    
    if not hist:
        return jsonify({"error": "版本不存在"}), 404
    
    return jsonify(dict(hist))

@app.route('/wiki/api/pages/<slug>/rollback/<int:version>', methods=['POST'])
def rollback_page(slug, version):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM pages WHERE slug = ?", (slug,))
    page = c.fetchone()
    if not page: conn.close(); return jsonify({"error": "页面不存在"}), 404
    c.execute("SELECT content, content_html FROM page_history WHERE page_id = ? AND version = ?", (page['id'], version))
    hist = c.fetchone()
    if not hist: conn.close(); return jsonify({"error": "版本不存在"}), 404
    new_c = hist['content'] if hist['content'] and hist['content'].strip() else hist['content_html']
    c.execute("UPDATE pages SET content = ?, content_html = ?, updated_at = datetime('now') WHERE id = ?", (new_c, hist['content_html'], page['id']))
    c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM page_history WHERE page_id = ?", (page['id'],))
    nv = c.fetchone()[0]
    c.execute("INSERT INTO page_history (page_id, content, content_html, edited_by, version, summary, edited_at) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
              (page['id'], hist['content'], hist['content_html'], 1, nv, '回滚到版本 ' + str(version)))
    conn.commit(); conn.close()
    return jsonify({"ok": True, "version": nv, "rolled_back_to": version})

@app.route('/wiki/api/pages', methods=['POST'])
@login_required
def create_page():
    data = request.get_json()
    if not data or not data.get('title'):
        return jsonify({"error": "标题不能为空"}), 400
    
    title = data['title'].strip()
    slug = data.get('slug', '').strip()
    if not slug:
        slug = slugify(title)
    
    parent_id = data.get('parent_id')
    content = data.get('content', '')
    content_html = md_to_html(content) if content else ''
    
    conn = get_db()
    c = conn.cursor()
    
    # 检查 slug 唯一性
    c.execute("SELECT id FROM pages WHERE slug = ?", (slug,))
    if c.fetchone():
        # 添加后缀
        base_slug = slug
        i = 1
        while True:
            slug = f"{base_slug}-{i}"
            c.execute("SELECT id FROM pages WHERE slug = ?", (slug,))
            if not c.fetchone():
                break
            i += 1
    
    # 获取最大 sort_order
    if parent_id:
        c.execute("SELECT COALESCE(MAX(sort_order), 0) + 10 FROM pages WHERE parent_id = ?", (parent_id,))
    else:
        c.execute("SELECT COALESCE(MAX(sort_order), 0) + 10 FROM pages WHERE parent_id IS NULL")
    sort_order = c.fetchone()[0]
    
    c.execute("""
        INSERT INTO pages (title, slug, parent_id, content, content_html, sort_order, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (title, slug, parent_id, content, content_html, sort_order, session['user_id']))
    
    page_id = c.lastrowid
    
    # 记录历史
    c.execute("""
        INSERT INTO page_history (page_id, content, content_html, edited_by, version, summary)
        VALUES (?, ?, ?, ?, 1, '创建页面')
    """, (page_id, content, content_html, session['user_id']))
    
    conn.commit()
    conn.close()
    
    return jsonify({"ok": True, "slug": slug, "id": page_id})

@app.route('/wiki/api/pages/<slug>', methods=['PUT'])
@login_required
def update_page(slug):
    data = request.get_json()
    if not data:
        return jsonify({"error": "数据不能为空"}), 400
    
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT * FROM pages WHERE slug = ?", (slug,))
    page = c.fetchone()
    if not page:
        conn.close()
        return jsonify({"error": "页面不存在"}), 404
    
    title = data.get('title', page['title']).strip()
    content = data.get('content', page['content'])
    # 如果传了 content_html（所见即所得编辑器），直接使用；否则从 content 转换
    content_html = data.get('content_html', md_to_html(content))
    summary = data.get('summary', '')
    
    # 获取新版本号
    c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM page_history WHERE page_id = ?", (page['id'],))
    new_version = c.fetchone()[0]
    
    c.execute("""
        UPDATE pages SET title = ?, content = ?, content_html = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
    """, (title, content, content_html, page['id']))
    
    # 记录历史
    c.execute("""
        INSERT INTO page_history (page_id, content, content_html, edited_by, version, summary)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (page['id'], content, content_html, session['user_id'], new_version, summary))
    
    conn.commit()
    conn.close()
    
    return jsonify({"ok": True, "version": new_version})

@app.route('/wiki/api/pages/<slug>', methods=['DELETE'])
@login_required
def delete_page(slug):
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id FROM pages WHERE slug = ?", (slug,))
    page = c.fetchone()
    if not page:
        conn.close()
        return jsonify({"error": "页面不存在"}), 404
    
    c.execute("UPDATE pages SET is_published = 0 WHERE id = ?", (page['id'],))
    conn.commit()
    conn.close()
    
    return jsonify({"ok": True})

@app.route('/wiki/api/pages/move', methods=['POST'])
@login_required
def move_page():
    data = request.get_json()
    page_id = data.get('page_id')
    new_parent_id = data.get('parent_id')  # None = 根目录
    
    conn = get_db()
    c = conn.cursor()
    c.execute("UPDATE pages SET parent_id = ? WHERE id = ?", (new_parent_id, page_id))
    conn.commit()
    conn.close()
    
    return jsonify({"ok": True})

@app.route('/wiki/api/pages/reorder', methods=['POST'])
@login_required
def reorder_pages():
    data = request.get_json()
    orders = data.get('orders', [])
    
    conn = get_db()
    c = conn.cursor()
    for item in orders:
        c.execute("UPDATE pages SET sort_order = ? WHERE id = ?", 
                  (item.get('sort_order', 0), item.get('id')))
    conn.commit()
    conn.close()
    
    return jsonify({"ok": True})

# ===== 路由：权限管理 =====
@app.route('/wiki/api/permissions/<int:page_id>', methods=['GET'])
@login_required
def get_permissions(page_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        SELECT pp.id, pp.permission, u.id as user_id, u.username, u.display_name
        FROM page_permissions pp
        JOIN users u ON pp.user_id = u.id
        WHERE pp.page_id = ?
    """, (page_id,))
    perms = c.fetchall()
    conn.close()
    return jsonify([dict(p) for p in perms])

@app.route('/wiki/api/permissions', methods=['POST'])
@login_required
def set_permission():
    data = request.get_json()
    page_id = data.get('page_id')
    user_id = data.get('user_id')
    permission = data.get('permission')
    
    if not all([page_id, user_id, permission]):
        return jsonify({"error": "参数不完整"}), 400
    if permission not in ('read', 'write', 'admin'):
        return jsonify({"error": "无效权限值"}), 400
    
    conn = get_db()
    c = conn.cursor()
    
    # 删除已有权限
    c.execute("DELETE FROM page_permissions WHERE page_id = ? AND user_id = ?",
              (page_id, user_id))
    
    # 添加新权限
    c.execute("""
        INSERT INTO page_permissions (page_id, user_id, permission)
        VALUES (?, ?, ?)
    """, (page_id, user_id, permission))
    
    conn.commit()
    conn.close()
    
    return jsonify({"ok": True})

@app.route('/wiki/api/permissions/<int:perm_id>', methods=['DELETE'])
@login_required
def remove_permission(perm_id):
    conn = get_db()
    c = conn.cursor()
    c.execute("DELETE FROM page_permissions WHERE id = ?", (perm_id,))
    conn.commit()
    conn.close()
    return jsonify({"ok": True})

# ===== 路由：用户管理（管理员） =====
@app.route('/wiki/api/users', methods=['GET'])
@admin_required
def get_users():
    conn = get_db()
    c = conn.cursor()
    c.execute("SELECT id, username, display_name, is_admin, created_at FROM users ORDER BY username")
    users = c.fetchall()
    conn.close()
    return jsonify([dict(u) for u in users])

@app.route('/wiki/api/users', methods=['POST'])
@admin_required
def create_user():
    data = request.get_json()
    username = data.get('username', '').strip()
    password = data.get('password', '')
    display_name = data.get('display_name', username)
    is_admin = data.get('is_admin', 0)
    
    if not username or not password:
        return jsonify({"error": "用户名和密码不能为空"}), 400
    if len(password) < 6:
        return jsonify({"error": "密码至少6位"}), 400
    
    password_hash = hashlib.sha256(password.encode()).hexdigest()
    
    conn = get_db()
    c = conn.cursor()
    try:
        c.execute("""
            INSERT INTO users (username, password_hash, display_name, is_admin)
            VALUES (?, ?, ?, ?)
        """, (username, password_hash, display_name, is_admin))
        conn.commit()
        user_id = c.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({"error": "用户名已存在"}), 400
    
    conn.close()
    return jsonify({"ok": True, "id": user_id})


@app.route('/wiki/api/users/<int:user_id>', methods=['PUT'])
@admin_required
def update_user(user_id):
    """更新用户信息（用户名、显示名、密码、管理员权限）"""
    data = request.get_json(silent=True) or {}
    conn = get_db()
    c = conn.cursor()
    
    c.execute("SELECT id FROM users WHERE id = ?", (user_id,))
    if not c.fetchone():
        conn.close()
        return jsonify({"error": "用户不存在"}), 404
    
    username = data.get('username', '').strip()
    if username:
        try:
            c.execute("UPDATE users SET username = ? WHERE id = ?", (username, user_id))
        except sqlite3.IntegrityError:
            conn.close()
            return jsonify({"error": "用户名已存在"}), 400
    
    display_name = data.get('display_name', '').strip()
    if display_name:
        c.execute("UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id))
    
    if 'is_admin' in data:
        c.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if data['is_admin'] else 0, user_id))
    
    password = data.get('password', '')
    if password:
        if len(password) < 6:
            conn.close()
            return jsonify({"error": "密码至少6位"}), 400
        password_hash = hashlib.sha256(password.encode()).hexdigest()
        c.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))
    
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "message": "用户信息已更新"})


# ===== 路由：文件上传 =====
@app.route('/wiki/api/upload', methods=['POST'])
def upload_file():
    """上传图片（去掉 login_required 让粘贴上传可用）"""
    if 'file' not in request.files:
        return jsonify({"error": "没有文件"}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({"error": "文件名为空"}), 400
    
    # 生成唯一文件名
    ext = os.path.splitext(file.filename)[1].lower()
    allowed = ['.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.bmp', '.ico']
    if ext not in allowed:
        return jsonify({"error": f"不支持的文件类型: {ext}"}), 400
    
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = UPLOAD_DIR / filename
    
    file.save(str(filepath))
    size = os.path.getsize(str(filepath))
    
    mime = mimetypes.guess_type(file.filename)[0] or 'application/octet-stream'
    
    conn = get_db()
    c = conn.cursor()
    c.execute("""
        INSERT INTO uploads (filename, original_name, filepath, mime_type, size, uploaded_by)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (filename, file.filename, str(filepath), mime, size, session.get('user_id', 1)))
    upload_id = c.lastrowid
    conn.commit()
    conn.close()
    
    return jsonify({
        "ok": True,
        "id": upload_id,
        "url": url_for('wiki_uploaded_file', filename=filename),
        "filename": filename,
        "original_name": file.filename,
        "size": size
    })

@app.route('/wiki/uploads/<filename>')
def wiki_uploaded_file(filename):
    return send_from_directory(str(UPLOAD_DIR), filename)

# ===== 工具函数 =====
@app.route('/wiki/api/preview', methods=['POST'])
@login_required
def preview_markdown():
    data = request.get_json()
    content = data.get('content', '') if data else ''
    html = md_to_html(content)
    return jsonify({"html": html})


def slugify(text):
    """将中文文本转换为 slug"""
    # 简单 slug 化：去特殊字符，用连字符连接
    text = text.lower().strip()
    text = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', text)
    text = re.sub(r'[-\s]+', '-', text)
    text = text.strip('-')
    if not text:
        text = f"page-{uuid.uuid4().hex[:8]}"
    return text

# ===== 启动 =====
if __name__ == '__main__':
    init_db()
    print(f"Wiki 数据库已初始化: {DB_PATH}")
    print(f"启动 Wiki 服务: http://{HOST}:{PORT}{APPLICATION_ROOT}")
    print("首次访问会自动创建管理员账号 (admin/admin123)")
    print("请尽快修改默认密码！")
    app.run(host=HOST, port=PORT)
