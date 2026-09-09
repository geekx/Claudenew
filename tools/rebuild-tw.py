"""Rebuild the inlined Tailwind CSS against the file's CURRENT class usage.
Scans the app markup only (vendor blobs stripped) so library text can't
pollute the class extraction."""
import re, subprocess, sys, os
HTML='/home/user/Claudenew/packing-pro.html'
s=open(HTML,encoding='utf-8').read()

# strip inlined vendor <script>/<style> blocks before scanning
scan = re.sub(r'<style id="tailwind-inlined">.*?</style>', '', s, flags=re.S)
scan = re.sub(r'<script id="(?:three|orbit|lucide|exceljs)-inlined">.*?</script>', '', scan, flags=re.S)
open('scan.html','w',encoding='utf-8').write(scan)
print('scan surface: %d KB (from %d KB)' % (len(scan)//1024, len(s)//1024))

open('tw.config.js','w').write("module.exports={content:['./scan.html'],theme:{extend:{}},plugins:[]};\n")
r=subprocess.run(['npx','tailwindcss','-c','tw.config.js','-i','tw.css','-o','tw.out.css','--minify'],
                 capture_output=True,text=True)
if r.returncode!=0:
    print(r.stderr); sys.exit(1)
css=open('tw.out.css',encoding='utf-8').read()
print('rebuilt tailwind: %d KB' % (len(css)//1024))

new_block='<style id="tailwind-inlined">/* Tailwind CSS v3.4.17 (MIT) — 已按本文件实际使用的类名裁剪 */\n'+css+'\n</style>'
s2=re.sub(r'<style id="tailwind-inlined">.*?</style>', lambda m: new_block, s, count=1, flags=re.S)
assert s2!=s, 'style block not replaced'
open(HTML,'w',encoding='utf-8').write(s2)
print('inlined. file: %d KB' % (len(s2.encode())//1024))
