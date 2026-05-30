# 行为习惯打分系统

小朋友（6岁）的行为习惯和作业完成打分系统。

## 功能

- ✅ 每日打分（按规则打分）
- ✅ 打卡日历（英语/学校/口算）
- ✅ 统计卡片（总得分/今日/本周/本月）
- ✅ 趋势图表
- ✅ 标题可编辑
- ✅ 规则分类管理（学习/习惯/运动/家务/其他）
- ✅ localStorage 持久化标题

## 使用

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
