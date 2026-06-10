# NCAT Smart Translator

桌面端文档翻译工具（pywebview + DeepSeek API），翻译 Word / PowerPoint / Excel
并尽量保留原有格式与版面。主程序：`NCAT_Smart_V3.py`。

## 核心能力

- **保留版面翻译**：逐段替换文本、按字号自适应缩放、支持双语对照、PPT 分组形状/表格/讲稿。
- **术语表（Glossary）**：`terms/` 目录下的 CSV，按文件开关，命中术语用 `<dnt>` 保护不被改写。
- **翻译记忆（TM）**：本地 SQLite，记录已译内容，下次智能复用。

## 高级选项（翻译模式后的「Advanced | 高级选项」页，逐项可开关）

| 选项 | 说明 | 默认 |
| --- | --- | --- |
| 🔒 机密切碎 Confidential | 翻译片段乱序 + 更小批次，任一次 API 请求都拿不到连贯原文，保护机密 | 开 |
| 🧠 智能记忆复用 Smart memory | TM 缓存命中（cache-hit）微调：归一化大小写/空白/收尾标点后仍能命中 | 开 |
| 🌉 小语种经英文中转 Pivot | 中文→中英以外小语种时，优先用 glossary 中英转英文，再由模型译为目标语种 | 开 |
| 💡 脑补检查 Context inference | 结合整页情景还原省略主语/动名词等隐含成分后再翻译（额外消耗 token） | 关 |
| ⚖️ 译法仲裁 Term arbitration | 翻译前抽取高频专业词并裁定唯一权威译法，全文强制统一（额外消耗 token） | 关 |

> 小语种翻译的两级优先级：① glossary 中英中转；② 由模型自行选择更准确的译法。

## 运行

```bash
pip install pywebview pystray pillow pandas openpyxl requests python-docx python-pptx pytz
python NCAT_Smart_V3.py
```

把术语 CSV 放在程序同目录的 `terms/` 下（列名可为 source/target、原文/译文等）。
翻译记忆库会自动生成在 `translation_memory.db`。
