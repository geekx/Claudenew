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

## 防漏译机制

- **二分递归对齐**：当 API 返回的分段数与输入不一致（分隔符被吞/段落被合并）时，
  自动把批次劈成两半分别重译，最终落到逐段翻译，从数学上保证「输出段数=输入段数」，
  既不漏译也不错位。
- **修复轮**：批量翻译后，对仍等于原文或语言不符的段落逐段补译一次。
- **完整性审计**：保存前统计「应译却仍为原文」的段落数，若有残留会显式报警
  （`⚠ N 段未译出`），漏译可见而非静默。型号/编号（如 ABC-123）不计入。
- **记忆库防污染**：译文=原文、或与目标语言明显不符的结果不写入 TM；
  每次会话首次加载时自动清洗历史污染记录。

## 测试

```bash
pip install python-docx       # 集成测试需要
python tests/run_all.py
```

- `tests/test_logic.py`：纯逻辑（TM 模糊命中/防污染、语言判定、英文中转、机密切碎、
  脑补、译法仲裁、二分递归零漏译），依赖打桩，无需 Office 库。
- `tests/test_word_integration.py`：真实 python-docx 端到端，验证正文+表格+文本框零漏译。
- `tests/test_ppt_integration.py`：真实 python-pptx 端到端，验证文本框+表格+组合形状+备注零漏译。
- `tests/test_excel_integration.py`：真实 openpyxl 端到端，验证文本单元格被译、公式单元格不被破坏。

## 路线图

- [x] **P0 零漏译**：二分递归对齐 + 修复轮 + 完整性审计 + TM 防污染（含测试）
- [x] **P1a Word 文本框**：DrawingML/VML 文本框抽取 + mc:Fallback 去重（端到端测试通过）
- [x] **P1a PPT 集成测试**：文本框/表格/组合/备注端到端零漏译验证
- [x] **P1b 过度翻译修复**：PPT 母版/版式占位样板字（“Click to edit…”、页码/日期占位）
      不再送译，只译真实自定义内容（端到端测试覆盖）
- [x] **TM 端到端复用验证**：同句二次翻译零 API 调用（精确+模糊命中，集成测试覆盖）
- [x] **Excel 公式保护**：公式单元格（含内嵌中文的 `=CONCATENATE(...)`）不再送译，
      避免破坏表格函数（端到端测试覆盖）
- [ ] **P1b Word 脚注/尾注**：待解决——python-docx 将 footnotes.xml 作为 blob 部件加载，
      改动解析副本不会在保存时写回；需先确认写回持久化方案再实现（否则只译不写=仍漏译）
- [ ] **P1b PPT 图表/SmartArt 内文字**
- [ ] **P2 版式精度**：保留多 run 段落的内联格式（粗体/斜体片段）、超链接关系
- [x] **P3 TM 导入导出**：记忆库 CSV 导出/导入（导入带语言符合性校验过滤污染），
      UI 记忆库栏加 ⬇/⬆ 按钮（往返测试覆盖）
- [x] **P3 仲裁术语沉淀**：译法仲裁的专业词自动写入 `terms/arbitrated_<目标语>.csv`
      （按目标语分文件、合并去重），下次经 glossary 系统自动复用，免重复抽词花 token
- [ ] **P3 工程化**：断点续译、批处理进度持久化
