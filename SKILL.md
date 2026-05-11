---
name: kuafuai
description: 淘宝UI自动化测试Skill - 接收飞书指令，完成商品搜索、筛选、加购全流程
version: 1.0.0
author: heyang
openclaw: 1.0
---

# Taobao UI Test Skill

## 概述

通过飞书接收测试任务指令，自动执行淘宝的商品搜索、好评率筛选、加入购物车操作，并将结果回传飞书。支持动态决策编排，可自适应页面变化。

## 输入参数

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| keyword | string | 否 | 搜索关键词，默认"索尼耳机" |
| min_rating | number | 否 | 最低好评率，默认 0.99 |
| platform | string | 否 | 目标平台，默认 "taobao" |

## 输出结果

| 字段 | 类型 | 说明 |
|------|------|------|
| status | string | success / failed / partial |
| products_added | int | 成功加入购物车的商品数 |
| details | list | 每个商品的名称、价格、好评率 |
| screenshots | list | 关键步骤截图路径 |
| error | string | 异常信息（如有） |

## 流程概览

```
飞书指令 → LLM Agent编排引擎 → [登录 → 搜索 → 筛选 → 加购] → 结果回传
```

编排方式为非硬编码动态决策，由 LLM Agent 根据当前页面状态决定下一步操作。

## 依赖

- Python 3.10+
- Playwright + playwright-stealth
- lark-oapi（飞书 SDK，长连接模式）
- OpenAI SDK（调用 DeepSeek API）

## 运行方式

```bash
# 安装依赖
pip install -r requirements.txt
playwright install chromium

# 启动（飞书长连接模式）
python skill_entry.py
```
