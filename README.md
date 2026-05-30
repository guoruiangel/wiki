# Private Wiki - 私有 Wiki 系统

一个轻量级的私有 Wiki 系统，使用 Python + Flask + SQLite 构建。

## 功能

- **所见即所得编辑** — 富文本编辑器，支持粗体、斜体、颜色、字号
- **图片支持** — 拖拽上传、编辑时拖拽调整大小
- **表格编辑** — 行列选择插入、行号、单元格颜色
- **树形页面导航** — 多级目录结构
- **权限管理** — 按页面设置用户读写权限
- **版本历史** — 每次编辑自动保存快照，可回溯
- **用户认证** — 登录系统，支持多用户
- **公开分享** — 可设置页面为公开访问

## 快速开始

```bash
# 1. 克隆
git clone <repo-url>
cd private-wiki

# 2. 安装依赖
pip install flask waitress

# 3. 初始化数据库（首次启动自动创建）
python3 wiki_app.py

# 4. 访问
http://localhost:5003/wiki/login
```

默认管理员账号：`admin`，密码：`admin123`（首次启动时自动创建，请立即修改）

## 项目结构

```
private-wiki/
├── wiki_app.py          # Flask 应用 + 路由 + API
├── wiki2.py             # 早期版本（保留参考）
├── templates/
│   ├── wiki.html        # 主页面模板（含全部前端逻辑）
│   └── login.html       # 登录页面
├── static/              # 静态资源
├── uploads/             # 上传文件目录
├── wiki.db              # SQLite 数据库（自动创建）
└── requirements.txt     # Python 依赖
```

## 配置

编辑 `wiki_app.py` 顶部的配置区域：

```python
SECRET_KEY = 'your-secret-key'     # 会话密钥
PORT = 5003                         # 监听端口
UPLOAD_FOLDER = 'uploads'           # 上传目录
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 最大上传 16MB
```

## 许可证

MIT
