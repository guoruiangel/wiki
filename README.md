# 📝 Private Wiki

<p align="center">
  <img src="https://img.shields.io/badge/python-3.8%2B-blue" alt="Python">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="License">
  <img src="https://img.shields.io/badge/flask-2.0%2B-lightblue" alt="Flask">
  <img src="https://img.shields.io/github/stars/guoruiangel/wiki?style=social" alt="Stars">
</p>

一个轻量级的私有 Wiki 系统 + 行为习惯打分系统，使用 **Python + Flask + SQLite** 构建，单文件部署，开箱即用。

---

## ✨ 功能

### Wiki
| 功能 | 说明 |
|------|------|
| **所见即所得编辑** | 富文本编辑器，支持粗体、斜体、字体颜色、字号、标题层级 |
| **图片拖拽调整** | 插入图片后可拖拽调整大小（50-1200px，保持宽高比） |
| **密码保护** | 单用户密码登录，可修改 |
| **Markdown 快捷键** | 支持常用的 Markdown 格式快捷输入 |
| **搜索** | 全文搜索文档内容 |

### 打分系统
| 功能 | 说明 |
|------|------|
| **每日打分** | 按规则给小朋友每日行为/作业打分 |
| **打卡日历** | 英语/学校/口算每日打卡，完整月份视图 |
| **统计卡片** | 总得分 / 今日 / 本周 / 本月 |
| **趋势图表** | 得分趋势曲线 + 累计曲线 |
| **标题可编辑** | 页面标题点击即可编辑，localStorage 持久化 |
| **规则分类** | 学习/习惯/运动/家务/其他，分类管理 |

## 使用

### Wiki
```bash
python wiki_app.py
```
访问 http://localhost:5003/wiki/

### 打分系统
```bash
pip install flask waitress
python app.py
```
访问 http://localhost:5000/child/scoring

## 技术栈

- Flask + Jinja2
- SQLite
- Chart.js
- Bootstrap 5
- Quill.js (富文本编辑器)

## 许可

MIT
