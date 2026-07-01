# 网页版（单文件 HTML + XOR-91 混淆）

- `shell.html`：页面外壳，包含所有 **SVG 图标资源**（定子本体、夹持爪、温度计、
  激光标记箭头、工序箭头），以 `<template>` 形式明文内嵌，未做任何混淆。
- `assets/*.svg`：用户提供的真实工艺图标原图（Cooling / Cure / EOL / Gel /
  Laser Welding / Paper insertion / PDIV / Pin forming / Pin insertion /
  Powder coating / preheating / Press / Tig / Tricking / Trimming /
  Twisting），逐一原样保存，未做任何修改。
- `app.src.js`：主渲染逻辑（工序状态表 + 图标拼装/旋转/上色），明文源码，供维护用。
- `build.py`：
  1. 读取 `assets/*.svg`，只做两件必要的事再内嵌进页面：
     - 缺 `viewBox` 的补上（原图只有 `width`/`height`，补 `viewBox` 才能在
       网格单元格里正确缩放）；
     - 给每个文件内部的 `id`（`clip0`/`img0` 这类通用命名）加上文件名前缀再
       同步替换所有 `url(#...)` / `href="#..."` 引用 —— 这些原图各自独立导出时
       内部 id 都不冲突，但同一页面内嵌多个后 id 会互相打架，导致
       `clip-path` 指向错误、图标整片空白，加前缀后各图标互不干扰。
  2. 读取 `app.src.js`，对其源码做 **XOR 91** 字节异或后 base64 编码，写入
     `shell.html` 的 `<script>` 占位处。
  最终写出 `index.html`；页面里只有一小段未混淆的 bootstrap 代码负责在运行时
  `atob` 解码、按位异或还原、`TextDecoder` 还原 UTF-8 文本，再用
  `new Function()` 执行还原后的逻辑。
- `index.html`：最终产物，双击即可在浏览器中打开查看图面（SVG 资源明文可见，
  主逻辑源码在页面源代码中只表现为一段 base64 密文）。

真实图标暂缺 Forming cutting / Straightening / Widening 三个工序，对应单元格
显示"(暂无原图)"占位，等提供原图后放入 `assets/` 并在 `app.src.js` 的
`REAL_ICON_MAP` 里加一行映射即可。

## 重新生成

```bash
cd stator_montage_tool/web
python3 build.py
```

修改 `app.src.js`（工序表 / 图标逻辑）或 `assets/*.svg` 后需重新运行
`build.py` 才会更新 `index.html`。

> 说明：XOR 91 是一种轻量级的"隐藏代码"手段（避免直接在页面源码里明文暴露工艺参数
> 和渲染逻辑），并非加密强度意义上的安全防护，任何人 view-source 后运行几行 JS
> 都能还原明文，如需真正的访问控制请另行评估。
