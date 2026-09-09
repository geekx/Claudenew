# 维护脚本

`packing-pro.html` 是完全离线的单文件应用，所有依赖已内嵌。
其中 Tailwind CSS 是**按当前文件实际用到的类名裁剪**过的，因此：

> ⚠️ 每次新增/修改带 Tailwind 类名的 HTML 后，必须重建内嵌 CSS，
> 否则新加的类没有对应样式规则，元素会静默失去样式（按钮看不见等）。

## 重建内嵌 Tailwind

```bash
cd <装有 tailwindcss 的目录>
python3 rebuild-tw.py
```

脚本会先剥离已内嵌的 vendor 代码块再扫描类名，避免库代码污染提取结果。

## 校验（提交前跑一次）

```bash
node csscheck.js
```

逐个检查页面用到的每个 Tailwind 类在内嵌 CSS 中是否存在对应规则，
缺失则以非零码退出并列出类名。装饰性动画类（`animate-in` 等，
原 CDN 版本亦未包含）会单独提示但不算失败。
