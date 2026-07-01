# 网页版（单文件 HTML + XOR-91 混淆）

- `shell.html`：页面外壳，包含所有 **SVG 图标资源**（定子本体、夹持爪、温度计、
  激光标记箭头、工序箭头），以 `<template>` 形式明文内嵌，未做任何混淆。
- `app.src.js`：主渲染逻辑（工序状态表 + 图标拼装/旋转/上色），明文源码，供维护用。
- `build.py`：读取 `app.src.js`，对其源码做 **XOR 91** 字节异或后 base64 编码，
  写入 `shell.html` 的 `<script>` 占位处，生成最终的 `index.html`。
  `index.html` 中只有一小段未混淆的 bootstrap 代码负责在运行时 `atob` 解码、
  按位异或还原、`TextDecoder` 还原 UTF-8 文本，再用 `new Function()` 执行还原后的逻辑。
- `index.html`：最终产物，双击即可在浏览器中打开查看图面（SVG 资源明文可见，
  主逻辑源码在页面源代码中只表现为一段 base64 密文）。

## 重新生成

```bash
cd stator_montage_tool/web
python3 build.py
```

修改 `app.src.js`（工序表 / 图标逻辑）后需重新运行 `build.py` 才会更新 `index.html`。

> 说明：XOR 91 是一种轻量级的"隐藏代码"手段（避免直接在页面源码里明文暴露工艺参数
> 和渲染逻辑），并非加密强度意义上的安全防护，任何人 view-source 后运行几行 JS
> 都能还原明文，如需真正的访问控制请另行评估。
