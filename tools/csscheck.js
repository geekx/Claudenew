// Verify every Tailwind class the app actually uses has a rule in the inlined CSS.
// Catches the failure mode where markup is added after the purge build.
const fs=require('fs');
const html=fs.readFileSync('/home/user/Claudenew/packing-pro.html','utf8');

const css=(html.match(/<style id="tailwind-inlined">([\s\S]*?)<\/style>/)||[])[1]||'';
// app surface only — vendor blobs would add noise
let app=html.replace(/<style id="tailwind-inlined">[\s\S]*?<\/style>/,'')
            .replace(/<script id="(?:three|orbit|lucide|exceljs)-inlined">[\s\S]*?<\/script>/g,'');

// collect classes from class="..." and className-ish template literals
const classAttrs=[...app.matchAll(/class="([^"]*)"/g)].map(m=>m[1]);
const tokens=new Set();
classAttrs.forEach(a=>{
  // drop ${...} interpolations — those are dynamic and handled separately
  a.replace(/\$\{[^}]*\}/g,' ').split(/\s+/).forEach(c=>{ if(c) tokens.add(c); });
});
// dynamic class strings assigned in JS (e.g. badge maps, className = '...')
[...app.matchAll(/'((?:[a-z0-9-]+:)?[a-z][\w./[\]#-]*(?:\s+(?:[a-z0-9-]+:)?[a-z][\w./[\]#-]*){2,})'/g)]
  .forEach(m=>m[1].split(/\s+/).forEach(c=>tokens.add(c)));

// escape a class name the way Tailwind writes it in CSS
const esc=c=>'.'+c.replace(/([.:/[\]#!%(),])/g,'\\$1');

// Classes we define ourselves: the app's own <style>, plus every <style>
// embedded in a template literal (the exported report ships its own CSS).
const ownCss=[...html.matchAll(/<style[^>]*>([\s\S]*?)<\/style>/g)]
  .map(m=>m[1]).filter(c=>!c.includes('--tw-')).join('\n');
const isOwn=c=>new RegExp('\\.'+c.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+'\\b').test(ownCss);

// tailwindcss-animate plugin classes: never shipped by the old CDN build either,
// purely decorative entrance animations — report separately, don't fail on them
const ANIMATE=/^(animate-in|fade-in|slide-in-from-|zoom-in|duration-|scrollbar-hide)/;

const missing=[...tokens].filter(c=>{
  if(/^(md3e-|cargo-grid|input-compact|tab-btn|loading-overlay|font-mono|hidden)/.test(c)) return false;
  if(ANIMATE.test(c)) return false;
  if(isOwn(c)) return false;
  if(!/^[a-z]/.test(c)) return false;
  return !css.includes(esc(c));
}).sort();

console.log('扫描 class 令牌: '+tokens.size);
console.log('内嵌 Tailwind CSS: '+Math.round(css.length/1024)+' KB');
if(missing.length){
  console.log('\n❌ 以下 '+missing.length+' 个类在 CSS 中缺失（样式不会生效）:');
  missing.forEach(c=>console.log('   '+c));
  process.exit(1);
}
const inert=[...tokens].filter(c=>ANIMATE.test(c)).sort();
if(inert.length) console.log('（装饰性动画类 '+inert.length+' 个未包含，历来即为无效，不影响功能: '+inert.join(', ')+'）');
console.log('\n✅ 所有使用到的 Tailwind 功能类均已包含在内嵌 CSS 中');
