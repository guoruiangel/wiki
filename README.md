# 📝 Private Wiki

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/flask-2.0%2B-lightblue" alt="Flask">
  <img src="https://img.shields.io/github/stars/guoruiangel/wiki?style=social" alt="Stars">
</p>

一个轻量级的私有 Wiki 系统，使用 **Python + Flask + SQLite** 构建，单文件部署，开箱即用。

> 无需数据库、无需 Node.js、无需 Docker，一个 `python3 wiki_app.py` 就能跑起来。

---

## ✨ 功能

| 功能 | 说明 |
|------|------|
| **所见即所得编辑** | 富文本编辑器，支持粗体、斜体、字体颜色、字号、标题层级 |
| **📸 图片支持** | 拖拽粘贴图片自动上传，编辑时右下角拖拽调整大小 |
| **📊 表格编辑** | 行列选择插入、行号显示、单元格背景色 |
| **📁 树形导航** | 多级页面目录结构，无限层级 |
| **🔒 权限管理** | 按页面设置每位用户的读/写/管理员权限 |
| **📋 版本历史** | 每次编辑自动保存快照，可查看和回溯历史版本 |
| **👥 用户认证** | 登录/登出系统，支持多用户（管理员可管理用户） |
| **🔗 公开分享** | 可设置页面为公开访问，无需登录 |
| **🌙 暗色主题** | 默认暗色主题，护眼舒适 |

## 🚀 快速开始

```bash
# 1. 克隆
git clone https://github.com/guoruiangel/wiki.git
cd wiki

# 2. 安装依赖
pip install flask waitress

# 3. 启动
python3 wiki_app.py
```

打开浏览器访问 **http://localhost:5003/wiki/** 👈

### 默认账号

| 用户名 | 密码 | 角色 |
|--------|------|------|
| admin | admin123 | 管理员 |

> ⚠️ 首次启动后请立即修改默认密码！

## 🏗️ 项目结构

```
wiki/
├── wiki_app.py          # 🎯 核心应用（~1100行）
│                        #   路由、API、数据库、认证、全部逻辑
├── templates/
│   ├── wiki.html        # 🎨 主页面（富文本编辑器、树导航、权限界面）
│   └── login.html       # 🔑 登录页
├── static/              # 🖼️ 静态资源（可选）
├── uploads/             # 📁 上传文件目录（自动创建）
└── wiki.db              # 🗄️ SQLite 数据库（自动创建）
```

## ⚙️ 配置

编辑 `wiki_app.py` 顶部的配置区域：

```python
WIKI_TITLE = "Private Wiki"           # Wiki 名称
SECRET_KEY = "change-this-to-something-secret"  # 会话密钥
PORT = 5003                            # 监听端口
HOST = "0.0.0.0"                       # 监听地址
APPLICATION_ROOT = "/wiki"             # URL 路径前缀
```

### 生产环境部署

```bash
pip install waitress
waitress-serve --port=5003 wiki_app:app
```

## 📸 截图

<details>
<summary>点击展开截图</summary>

**页面浏览**
![页面浏览](https://via.placeholder.com/800x450/1a1a2e/ffffff?text=Page+View)

**富文本编辑**
![富文本编辑](https://via.placeholder.com/800x450/1a1a2e/ffffff?text=Rich+Text+Editor)

**权限管理**
![权限管理](https://via.placeholder.com/800x450/1a1a2e/ffffff?text=Permission+Manager)

</details>

## 🧩 技术栈

- **后端**: Python 3 + Flask + SQLite (WAL 模式)
- **前端**: 原生 JavaScript + CSS3（无框架依赖）
- **编辑器**: contenteditable + document.execCommand
- **语言检测**: `.gitattributes` 强制 Python 识别

## 🌟 为什么做这个？

最初是为家庭成员（孩子）做一个个人知识库，可以记录学习笔记、打卡记录、共享资料。后来发现日常使用很方便，就整理成了开源项目。

**适合场景：**
- 🏠 家庭知识库 / 共享笔记
- 👨‍👩‍👧‍👦 小朋友学习记录
- 📚 个人 Wiki / 第二大脑
- 🔒 需要权限控制的小团队文档

## 📄 许可证

MIT — 随便用，随便改。
