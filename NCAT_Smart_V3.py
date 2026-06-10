# Sentient Translator - Enhanced Version with Layout Sensing
# 增强版：布局感知、Group形状支持、Notes讲稿翻译、翻译记忆、正式度控制
import os
import re
import glob
import json
import pytz
import random
import sqlite3
import time
import uuid
import datetime
import threading
import webview
import asyncio
from PIL import Image, ImageDraw, ImageFont
from pystray import Icon as TrayIcon, MenuItem as TrayMenuItem

import pandas as pd
import openpyxl
import requests
import docx
from pptx import Presentation
from pptx.util import Pt, Emu
from pptx.enum.shapes import MSO_SHAPE_TYPE
from pptx.dml.color import RGBColor
from typing import Dict, Tuple, List, Any, Set, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION & GLOBAL VARIABLES ---
# Generate a session-unique separator to prevent content collision
_SESSION_SEP_KEY = uuid.uuid4().hex[:12]
DEFAULT_CONFIG = {
    "BATCH_SIZE": 30,
    "TERM_SEPARATOR": f"|||—{_SESSION_SEP_KEY}—|||",
    "TERMS_DIR": "terms",
    "OUTPUT_SUFFIX": "--OP",
    "TM_DB": "translation_memory.db",
    "API_RETRY_COUNT": 3,
    "API_RETRY_DELAY": 2,
    "MAX_TOKENS_TRANSLATE": 8192,
    "MAX_TOKENS_PROOFREAD": 4096,
    # deepseek-chat/deepseek-reasoner deprecated 2026-07-24; use v4 IDs
    "MODEL_FAST": "deepseek-v4-flash",    # default: fast, cost-effective
    "MODEL_QUALITY": "deepseek-v4-pro",   # optional: higher quality
    # --- Advanced feature tuning | 高级功能调参 ---
    "PIVOT_LANGUAGE": "English",          # 小语种中转语言：中文->英文->目标语种
    "CONFIDENTIAL_BATCH_SIZE": 10,        # 机密模式下更小的批次，配合乱序切碎上下文
    "CONTEXT_MAX_CHARS": 1200,            # 脑补检查：传给模型的上下文最大字符数
    "ARBITRATION_MAX_CHARS": 6000,        # 译法仲裁：抽词时送检的源文最大字符数
    "ARBITRATION_MAX_TERMS": 40,          # 译法仲裁：最多仲裁的高频专业词数量
}

# 字号调整配置 - 中文到西文的字号缩放比例
FONT_SIZE_CONFIG = {
    "chinese_to_western_ratio": 0.85,  # 中文翻译为西文时，字号缩小到85%
    "min_font_size_pt": 8,             # 最小字号（磅）
    "max_font_size_pt": 96,            # 最大字号（磅）
    "title_threshold_pt": 24,          # 大于此字号视为标题，缩放比例更保守
    "title_ratio": 0.90,               # 标题的缩放比例
}

TIMEZONE_TO_LANGUAGE_MAP = { 'Europe': 'German', 'Asia': 'Chinese (Simplified)', 'America': 'English', 'Australia': 'English' }
DEFAULT_TARGET_LANGUAGE = 'English'
WINDOWS_TO_IANA_MAP = { "China Standard Time": "Asia/Shanghai", "Tokyo Standard Time": "Asia/Tokyo", "Korea Standard Time": "Asia/Seoul", "GMT Standard Time": "Europe/London", "W. Europe Standard Time": "Europe/Berlin", "Central Europe Standard Time": "Europe/Paris", "Eastern Standard Time": "America/New_York", "Pacific Standard Time": "America/Los_Angeles" }
WINDOW = None
TRAY_ICON = None
TERMS_STATUS = None
POETRY_QUOTE = "山重水复疑无路，柳暗花明又一村"
GLOSSARY_ENABLED = True  # 术语表总开关
GLOSSARY_FILES = {}  # 每个术语文件的状态: {filename: {'enabled': True, 'terms': 10, 'path': '...'}, ...}

_startup_futures = {}
_background_loader = None
_translation_memory: Optional['TranslationMemory'] = None


# --- TRANSLATION MEMORY ---

class TranslationMemory:
    """本地翻译记忆库 - SQLite支持，类似Trados TM"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        with self._get_conn() as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS tm (
                source_text TEXT NOT NULL,
                target_text TEXT NOT NULL,
                source_lang TEXT NOT NULL,
                target_lang TEXT NOT NULL,
                norm_key TEXT,
                use_count INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (source_text, source_lang, target_lang)
            )''')
            # 兼容旧库：补上 norm_key 列与索引（cache-hit 微调）
            cols = [r[1] for r in conn.execute("PRAGMA table_info(tm)").fetchall()]
            if 'norm_key' not in cols:
                conn.execute('ALTER TABLE tm ADD COLUMN norm_key TEXT')
                for src, slang, tlang in conn.execute(
                        'SELECT source_text, source_lang, target_lang FROM tm').fetchall():
                    conn.execute(
                        'UPDATE tm SET norm_key=? WHERE source_text=? AND source_lang=? AND target_lang=?',
                        (normalize_tm_key(src), src, slang, tlang)
                    )
            conn.execute('CREATE INDEX IF NOT EXISTS idx_tm_norm ON tm (norm_key, source_lang, target_lang)')

    def _get_conn(self):
        return sqlite3.connect(self.db_path, check_same_thread=False)

    def lookup(self, text: str, source_lang: str, target_lang: str, fuzzy: bool = True) -> Optional[str]:
        """
        查找翻译记忆。
        先尝试精确匹配；若开启 fuzzy，则用归一化键命中大小写/空白/标点的细微差异。
        """
        if not text or not text.strip():
            return None
        slang, tlang = source_lang.lower(), target_lang.lower()
        with self._lock:
            with self._get_conn() as conn:
                row = conn.execute(
                    'SELECT target_text FROM tm WHERE source_text=? AND source_lang=? AND target_lang=?',
                    (text.strip(), slang, tlang)
                ).fetchone()
                if row:
                    conn.execute(
                        'UPDATE tm SET use_count=use_count+1, updated_at=datetime("now") WHERE source_text=? AND source_lang=? AND target_lang=?',
                        (text.strip(), slang, tlang)
                    )
                    return row[0]
                if fuzzy:
                    nk = normalize_tm_key(text)
                    if nk:
                        frow = conn.execute(
                            'SELECT source_text, target_text FROM tm WHERE norm_key=? AND source_lang=? AND target_lang=? ORDER BY use_count DESC LIMIT 1',
                            (nk, slang, tlang)
                        ).fetchone()
                        if frow:
                            conn.execute(
                                'UPDATE tm SET use_count=use_count+1, updated_at=datetime("now") WHERE source_text=? AND source_lang=? AND target_lang=?',
                                (frow[0], slang, tlang)
                            )
                            return frow[1]
                return None

    def store(self, source: str, target: str, source_lang: str, target_lang: str):
        """存储翻译对到记忆库"""
        if not source or not target or not source.strip() or not target.strip():
            return
        with self._lock:
            with self._get_conn() as conn:
                conn.execute(
                    '''INSERT INTO tm (source_text, target_text, source_lang, target_lang, norm_key)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(source_text, source_lang, target_lang)
                       DO UPDATE SET target_text=excluded.target_text, norm_key=excluded.norm_key, use_count=use_count+1, updated_at=datetime("now")''',
                    (source.strip(), target.strip(), source_lang.lower(), target_lang.lower(), normalize_tm_key(source))
                )

    def store_batch(self, pairs: List[Tuple[str, str]], source_lang: str, target_lang: str):
        """批量存储翻译对"""
        if not pairs:
            return
        with self._lock:
            with self._get_conn() as conn:
                conn.executemany(
                    '''INSERT INTO tm (source_text, target_text, source_lang, target_lang, norm_key)
                       VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(source_text, source_lang, target_lang)
                       DO UPDATE SET target_text=excluded.target_text, norm_key=excluded.norm_key, use_count=use_count+1, updated_at=datetime("now")''',
                    [(s.strip(), t.strip(), source_lang.lower(), target_lang.lower(), normalize_tm_key(s)) for s, t in pairs if s and t]
                )

    def get_stats(self) -> Dict:
        """获取TM统计信息"""
        with self._lock:
            with self._get_conn() as conn:
                total = conn.execute('SELECT COUNT(*) FROM tm').fetchone()[0]
                lang_pairs = conn.execute(
                    'SELECT source_lang, target_lang, COUNT(*) as cnt FROM tm GROUP BY source_lang, target_lang ORDER BY cnt DESC LIMIT 5'
                ).fetchall()
                return {
                    'total': total,
                    'lang_pairs': [{'source': r[0], 'target': r[1], 'count': r[2]} for r in lang_pairs]
                }

    def clear(self):
        """清除所有TM记录"""
        with self._lock:
            with self._get_conn() as conn:
                conn.execute('DELETE FROM tm')


def get_tm() -> Optional[TranslationMemory]:
    """获取全局TM实例（懒加载）"""
    global _translation_memory
    if _translation_memory is None:
        try:
            db_path = os.path.join(get_script_directory(), DEFAULT_CONFIG['TM_DB'])
            _translation_memory = TranslationMemory(db_path)
            print(f"📝 TM loaded: {db_path}")
        except Exception as e:
            print(f"TM initialization error: {e}")
    return _translation_memory


# --- UTILITY FUNCTIONS & CLASSES ---

class ProgressCallback:
    """统一的进度回调处理类"""
    def __init__(self, window):
        self.window = window

    def __call__(self, message_type: str, message: str):
        if not self.window: return
        safe_message = (str(message).replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r'))
        safe_type = str(message_type).replace('"', '\\"').replace("'", "\\'")
        try:
            js_code = f'window.handle_message({{"type": "{safe_type}", "text": "{safe_message}"}})'
            self.window.evaluate_js(js_code)
        except Exception as e:
            print(f"Error sending callback message: {e}")

    def status(self, message: str): self(message_type="status", message=message)
    def progress(self, message: str): self(message_type="progress", message=message)
    def error(self, message: str): self(message_type="error", message=message)
    def info(self, message: str): self(message_type="info", message=message)
    def batch_progress(self, current: int, total: int, filename: str): self(message_type="batch_progress", message=f"{current}/{total}|{filename}")
    def file_done(self, filename: str): self(message_type="file_done", message=filename)
    def clear_status(self): self(message_type="clear", message="")


def create_tray_icon():
    """创建托盘图标"""
    try:
        img = Image.new('RGBA', (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse([4, 4, 28, 28], fill=(3, 218, 198, 255))
        draw.ellipse([8, 8, 24, 24], fill=(255, 255, 255, 255))
        draw.ellipse([14, 14, 18, 18], fill=(3, 218, 198, 255))
        return img
    except Exception:
        return None


def is_chinese_text(text: str) -> bool:
    """检测文本是否主要为中文"""
    if not text:
        return False
    chinese_chars = sum(1 for c in text if '\u4e00' <= c <= '\u9fff')
    return chinese_chars / max(len(text.replace(' ', '')), 1) > 0.3


def is_chinese_to_western(source_lang: str, target_lang: str) -> bool:
    """判断是否为中文翻译为西文"""
    chinese_langs = {'chinese', 'chinese (simplified)', 'chinese (traditional)', 'mandarin', 'cantonese', 'zh', 'zh-cn', 'zh-tw'}
    western_langs = {'english', 'german', 'french', 'spanish', 'italian', 'portuguese', 'dutch', 'swedish', 'norwegian', 'danish', 'finnish'}
    return source_lang.lower() in chinese_langs and target_lang.lower() in western_langs


_CHINESE_LANG_KEYS = {'chinese', 'chinese (simplified)', 'chinese (traditional)', 'mandarin', 'cantonese', 'zh', 'zh-cn', 'zh-tw', '中文', '简体', '繁体'}
_ENGLISH_LANG_KEYS = {'english', 'en', 'en-us', 'en-gb', '英文', '英语'}


def _is_chinese_lang(lang: str) -> bool:
    return (lang or '').strip().lower() in _CHINESE_LANG_KEYS


def _is_english_lang(lang: str) -> bool:
    return (lang or '').strip().lower() in _ENGLISH_LANG_KEYS


def is_minor_language_target(source_lang: str, target_lang: str) -> bool:
    """
    判断是否为「中文 -> 中英以外的小语种」。
    这类语对模型直译往往不够准确，适合先经英文中转。
    """
    return _is_chinese_lang(source_lang) and not _is_english_lang(target_lang) and not _is_chinese_lang(target_lang)


def normalize_tm_key(text: str) -> str:
    """
    生成翻译记忆的归一化键（cache-hit 微调）。
    折叠空白、去除首尾标点、对非中文字符做小写，
    让大小写/空格/收尾标点的细微差异仍能命中记忆库。
    """
    if not text:
        return ""
    t = text.strip()
    # 折叠所有空白为单个空格
    t = re.sub(r'\s+', ' ', t)
    # 去除首尾常见标点（中英）
    t = t.strip(' \t\r\n.,;:!?，。；：！？、…·"\'“”‘’()（）[]【】<>《》-—_')
    # 非中文整体转小写（中文 lower() 无影响，但保持稳定）
    t = t.lower()
    return t


def build_context_hint(texts: List[str], max_chars: int = None) -> str:
    """
    脑补检查：从待译文本中拼出一段「情景上下文」，
    供模型在翻译时还原省略主语、动名词等隐含成分。
    """
    if not texts:
        return ""
    if max_chars is None:
        max_chars = DEFAULT_CONFIG.get('CONTEXT_MAX_CHARS', 1200)
    seen = set()
    parts = []
    total = 0
    for t in texts:
        s = (t or '').strip()
        if not s or s in seen:
            continue
        seen.add(s)
        parts.append(s)
        total += len(s)
        if total >= max_chars:
            break
    hint = " / ".join(parts)
    if len(hint) > max_chars:
        hint = hint[:max_chars]
    return hint


def calculate_adjusted_font_size(original_size_pt: float, source_lang: str, target_lang: str, original_text: str, translated_text: str) -> Optional[float]:
    """
    计算调整后的字号
    - 中文翻译为西文时，根据文本长度变化和配置比例调整字号
    - 返回None表示不需要调整
    """
    if not is_chinese_to_western(source_lang, target_lang):
        return None
    
    if not original_text or not translated_text:
        return None
    
    # 计算文本长度变化比例
    original_len = len(original_text.replace(' ', ''))
    translated_len = len(translated_text.replace(' ', ''))
    
    if original_len == 0:
        return None
    
    length_ratio = translated_len / original_len
    
    # 如果翻译后文本更短或差不多长，不需要调整
    if length_ratio <= 1.1:
        return None
    
    # 根据字号大小选择缩放比例
    if original_size_pt >= FONT_SIZE_CONFIG["title_threshold_pt"]:
        base_ratio = FONT_SIZE_CONFIG["title_ratio"]
    else:
        base_ratio = FONT_SIZE_CONFIG["chinese_to_western_ratio"]
    
    # 根据长度变化动态调整
    # 如果翻译后文本长度是原来的2倍以上，进一步缩小字号
    if length_ratio > 2.0:
        dynamic_ratio = base_ratio * 0.9
    elif length_ratio > 1.5:
        dynamic_ratio = base_ratio * 0.95
    else:
        dynamic_ratio = base_ratio
    
    new_size = original_size_pt * dynamic_ratio
    
    # 确保字号在合理范围内
    new_size = max(FONT_SIZE_CONFIG["min_font_size_pt"], min(FONT_SIZE_CONFIG["max_font_size_pt"], new_size))
    
    return new_size


def capitalize_first_letter(text: str) -> str:
    """将文本首字母大写"""
    if not text or len(text) == 0:
        return text
    for i, char in enumerate(text):
        if char.isalpha():
            return text[:i] + char.upper() + text[i+1:]
    return text


def detect_mixed_language_segments(text: str, expected_lang: str) -> List[Tuple[str, bool]]:
    """检测文本中的混合语言片段"""
    if not text.strip():
        return [(text, False)]
    if re.match(r'^[\d\s\-.,;:!?()（）【】\[\]{}""''""《》<>、。，；：！？]*$', text):
        return [(text, False)]
    return [(text, True)]


def clean_dnt_tags(text: str) -> str:
    """
    清理文本中残留的<dnt>标签，处理API各种变体输出
    """
    if not text:
        return text
    # Handle paired tags with content (including whitespace variants)
    cleaned = re.sub(r'<\s*dnt\s*>(.*?)<\s*/\s*dnt\s*>', r'\1', text, flags=re.IGNORECASE | re.DOTALL)
    # Handle standalone opening/closing tags
    cleaned = re.sub(r'<\s*/?\s*dnt\s*>', '', cleaned, flags=re.IGNORECASE)
    # Handle when API wraps the tag in quotes: "<dnt>term</dnt>" → term
    cleaned = re.sub(r'["“”]<\s*dnt\s*>(.*?)<\s*/\s*dnt\s*>["“”]', r'\1', cleaned, flags=re.IGNORECASE | re.DOTALL)
    # Handle escaped variants: &lt;dnt&gt;
    cleaned = re.sub(r'&lt;\s*dnt\s*&gt;(.*?)&lt;\s*/\s*dnt\s*&gt;', r'\1', cleaned, flags=re.IGNORECASE | re.DOTALL)
    cleaned = re.sub(r'&lt;\s*/?\s*dnt\s*&gt;', '', cleaned, flags=re.IGNORECASE)
    return cleaned


def detect_text_language_simple(text: str) -> str:
    """
    简单的语言检测（不使用API）
    返回: 'chinese', 'english', 'mixed', 'other'
    """
    if not text or not text.strip():
        return 'other'
    
    # 统计字符类型
    chinese_chars = 0
    english_chars = 0
    other_chars = 0
    
    for char in text:
        if '\u4e00' <= char <= '\u9fff':  # 中文字符范围
            chinese_chars += 1
        elif char.isalpha() and char.isascii():  # 英文字母
            english_chars += 1
        elif not char.isspace() and not char.isdigit():
            other_chars += 1
    
    total = chinese_chars + english_chars + other_chars
    if total == 0:
        return 'other'
    
    chinese_ratio = chinese_chars / total
    english_ratio = english_chars / total
    
    if chinese_ratio > 0.5:
        return 'chinese'
    elif english_ratio > 0.5:
        return 'english'
    elif chinese_ratio > 0.2 and english_ratio > 0.2:
        return 'mixed'
    else:
        return 'other'


def is_text_already_target_language(text: str, target_lang: str) -> bool:
    """
    检测文本是否已经是目标语言
    如果是，则不需要翻译
    """
    if not text or not text.strip():
        return True  # 空文本不需要翻译
    
    detected = detect_text_language_simple(text)
    target_lower = target_lang.lower()
    
    # 检查目标语言是否匹配检测结果
    if detected == 'chinese':
        return any(lang in target_lower for lang in ['chinese', 'mandarin', 'zh', '中文', '简体', '繁体'])
    elif detected == 'english':
        return any(lang in target_lower for lang in ['english', 'en', '英文', '英语'])
    elif detected == 'mixed':
        return False  # 混合语言总是需要翻译
    else:
        return False  # 其他语言需要进一步处理


class BackgroundLoader:
    """后台加载器"""
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=3)
        self.terms_future = None
        self.poetry_future = None
        
    def start_loading(self):
        self.terms_future = self.executor.submit(self._load_terms)
        self.poetry_future = self.executor.submit(self._load_poetry)
        
    def _load_terms(self):
        try:
            return load_terms_sync()
        except Exception as e:
            print(f"Error loading terms in background: {e}")
            return {}, None, "Terms loading failed | 术语加载失败"
    
    def _load_poetry(self):
        try:
            quotes = [
                "山重水复疑无路，柳暗花明又一村！",
                "宝剑锋从磨砺出，梅花香自苦寒来！", 
                "千磨万击还坚劲，任尔东西南北风！",
                "海内存知己，天涯若比邻！",
                "路漫漫其修远兮，吾将上下而求索！"
            ]
            return random.choice(quotes)
        except Exception:
            return "山重水复疑无路，柳暗花明又一村！"
    
    def get_terms(self, timeout=5):
        try:
            if self.terms_future:
                return self.terms_future.result(timeout=timeout)
        except Exception as e:
            print(f"Timeout or error getting terms: {e}")
        return {}, None, "Terms not ready | 术语未就绪"
    
    def get_poetry(self, timeout=2):
        try:
            if self.poetry_future:
                return self.poetry_future.result(timeout=timeout)
        except Exception as e:
            print(f"Timeout or error getting poetry: {e}")
        return "山重水复疑无路，柳暗花明又一村！"
    
    def shutdown(self):
        try:
            self.executor.shutdown(wait=False)
        except Exception:
            pass


class DeepSeekTranslator:
    """翻译器类 - 增强版，支持地道表达"""
    def __init__(self, api_key: str, temperature: float):
        self.api_key = api_key
        self.temperature = max(0.0, min(2.0, temperature))
        self.session = requests.Session()
        self.base_url = "https://api.deepseek.com/v1/chat/completions"

    def validate_api_key(self) -> Tuple[bool, str]:
        try:
            test_payload = {"model": DEFAULT_CONFIG['MODEL_FAST'], "messages": [{"role": "user", "content": "Hi"}], "max_tokens": 10, "temperature": 0.1}
            response = self.session.post(self.base_url, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=test_payload, timeout=15)
            if response.status_code == 200: return True, "API key is valid | API密钥有效"
            elif response.status_code == 401: return False, "Invalid API key | API密钥无效"
            elif response.status_code == 429: return False, "Rate limit exceeded | 请求频率超限"
            elif response.status_code == 403: return False, "API access forbidden | API访问被禁止"
            else: return False, f"API error (HTTP {response.status_code})"
        except requests.exceptions.Timeout: return False, "Request timeout | 请求超时"
        except requests.exceptions.ConnectionError: return False, "Connection error | 连接错误"
        except Exception as e: return False, f"Validation error: {str(e)}"

    def detect_language(self, text_samples: List[str]) -> str:
        if not text_samples: return "English"
        try:
            sample_text = " ".join(text_samples)
            payload = { "model": DEFAULT_CONFIG['MODEL_FAST'], "messages": [{"role": "system", "content": "You are a language detection expert. Identify the language of the given text and respond with only the language name in English. Do not provide any explanation."}, {"role": "user", "content": f"Detect the language of this text: {sample_text}"}], "temperature": 0.1, "max_tokens": 20 }
            response = self.session.post(self.base_url, headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}, json=payload, timeout=10)
            if response.status_code == 200:
                result = response.json()
                detected_lang = result.get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                return detected_lang if detected_lang else "English"
            return "English"
        except Exception as e:
            print(f"Language detection error: {e}")
            return "English"

    def create_batch_payload(self, texts: List[str], source_lang: str, target_lang: str, formality: str = "auto", context_hint: str = "") -> dict:
        """创建批量翻译请求体 - 增强版，支持正式度控制、脑补上下文"""

        # 正式度指令
        formality_map = {
            "formal": f"Use formal, professional {target_lang}. Prefer complete sentences, no contractions, polished register.",
            "informal": f"Use conversational, natural {target_lang}. Contractions and relaxed phrasing are fine.",
            "auto": "",
        }
        formality_instruction = formality_map.get(formality, "")

        if is_chinese_to_western(source_lang, target_lang):
            style_instruction = (
                f"STYLE for {source_lang}→{target_lang}:\n"
                f"1. Use natural, idiomatic {target_lang} - avoid word-for-word translation.\n"
                f"2. Adapt cultural idioms to {target_lang} equivalents where appropriate.\n"
                f"3. Keep translation concise; {target_lang} typically needs fewer characters than Chinese.\n"
                f"4. Prefer active voice.\n"
                + (f"5. {formality_instruction}\n" if formality_instruction else "")
            )
        else:
            style_instruction = (f"STYLE: {formality_instruction}\n" if formality_instruction else "")

        # 脑补检查指令：用整页情景还原省略主语/动名词等隐含成分后再翻译
        context_instruction = ""
        if context_hint:
            context_instruction = (
                f"CONTEXT AWARENESS (use to disambiguate, DO NOT translate this block):\n"
                f"The segments come from one document/page. Surrounding context:\n"
                f"\"\"\"{context_hint}\"\"\"\n"
                f"When a segment is elliptical (omitted subject/object), a bare nominalization, "
                f"or an abbreviated phrase, infer the intended full meaning from this context, "
                f"then translate that intended meaning naturally into {target_lang}. "
                f"Never output the context itself, only the translated segments.\n\n"
            )

        sep = DEFAULT_CONFIG['TERM_SEPARATOR']
        system_content = (
            f"You are a professional translator specializing in {source_lang} to {target_lang} translation. "
            f"Translations must be natural, fluent, and culturally appropriate.\n\n"
            f"{style_instruction}"
            f"{context_instruction}"
            f"TECHNICAL RULES:\n"
            f"1. Text inside <dnt>…</dnt> tags MUST NOT be translated. Output the content as-is, without the tags.\n"
            f"   Example: 'The <dnt>CPU</dnt> runs hot' → 'Die CPU läuft heiß' (German)\n"
            f"2. Never add, remove, or rewrite <dnt> tags. Never translate tag contents.\n"
            f"3. Translate all other text fully into {target_lang}.\n\n"
            f"FORMAT RULES (critical):\n"
            f"- Segments are separated by the exact string: {sep}\n"
            f"- Output ONLY translated segments, separated by that same string.\n"
            f"- Segment count in output MUST equal segment count in input. Do not merge or split segments.\n"
            f"- No explanations, no extra text, no markdown."
        )

        user_content = sep.join(texts)
        return {
            "model": DEFAULT_CONFIG['MODEL_FAST'],
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content}
            ],
            "temperature": self.temperature,
            "max_tokens": DEFAULT_CONFIG['MAX_TOKENS_TRANSLATE'],
            "stream": False
        }

    def translate_batch(self, texts: List[str], source_lang: str, target_lang: str, formality: str = "auto", context_hint: str = "") -> List[str]:
        if not texts:
            return []
        payload = self.create_batch_payload(texts, source_lang, target_lang, formality, context_hint)
        sep = DEFAULT_CONFIG['TERM_SEPARATOR']
        retries = DEFAULT_CONFIG['API_RETRY_COUNT']
        delay = DEFAULT_CONFIG['API_RETRY_DELAY']

        for attempt in range(retries):
            try:
                resp = self.session.post(
                    self.base_url,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=180
                )

                # Retry on rate limit or server error
                if resp.status_code in (429, 500, 502, 503):
                    wait = delay * (2 ** attempt)
                    print(f"API {resp.status_code}: retrying in {wait}s (attempt {attempt+1}/{retries})")
                    time.sleep(wait)
                    continue

                resp.raise_for_status()
                content = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
                if not content:
                    return texts

                translated_segments = [clean_dnt_tags(t.strip()) for t in content.split(sep)]

                if len(translated_segments) == len(texts):
                    return translated_segments

                # Segment count mismatch: try per-element retry for mismatched batches
                print(f"⚠️ Segment mismatch: expected {len(texts)}, got {len(translated_segments)}. Retrying individually...")
                if len(texts) <= 5:
                    # Small batch - retry one-by-one
                    results = []
                    for t in texts:
                        individual = self._translate_single(t, source_lang, target_lang, formality, context_hint)
                        results.append(individual)
                    return results

                # Large batch mismatch: pad/truncate as last resort
                if len(translated_segments) < len(texts):
                    return translated_segments + texts[len(translated_segments):]
                return translated_segments[:len(texts)]

            except Exception as e:
                if attempt < retries - 1:
                    wait = delay * (2 ** attempt)
                    print(f"API error (attempt {attempt+1}/{retries}): {e}. Retrying in {wait}s")
                    time.sleep(wait)
                else:
                    print(f"\nAPI request failed after {retries} attempts: {e}")

        return texts

    def _translate_single(self, text: str, source_lang: str, target_lang: str, formality: str = "auto", context_hint: str = "") -> str:
        """翻译单个文本段落（用于批量失败时的降级处理）"""
        if not text:
            return text
        sep = DEFAULT_CONFIG['TERM_SEPARATOR']
        try:
            payload = self.create_batch_payload([text], source_lang, target_lang, formality, context_hint)
            resp = self.session.post(
                self.base_url,
                headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                json=payload,
                timeout=60
            )
            resp.raise_for_status()
            content = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')
            if content:
                parts = [clean_dnt_tags(p.strip()) for p in content.split(sep) if p.strip()]
                return parts[0] if parts else text
        except Exception as e:
            print(f"Single translate error: {e}")
        return text

    def arbitrate_terms(self, texts: List[str], source_lang: str, target_lang: str,
                        existing_terms: Optional[Set[str]] = None) -> Dict[str, str]:
        """
        译法仲裁：从文档抽取高频专业词（剔除常用词与已有术语），
        为每个词裁定唯一权威译法，返回 {源词: 目标译法}。
        这只发一次 API 请求，作为可选项（会额外消耗 token）。
        小语种目标会沿用「中->英中转」语言裁定，保证一致性。
        """
        if not texts:
            return {}

        # 拼接源文（去重、限长以控制 token）
        max_chars = DEFAULT_CONFIG.get('ARBITRATION_MAX_CHARS', 6000)
        max_terms = DEFAULT_CONFIG.get('ARBITRATION_MAX_TERMS', 40)
        corpus_parts, seen, total = [], set(), 0
        for t in texts:
            s = (t or '').strip()
            if not s or s in seen:
                continue
            seen.add(s)
            corpus_parts.append(s)
            total += len(s)
            if total >= max_chars:
                break
        corpus = "\n".join(corpus_parts)
        if not corpus.strip():
            return {}

        existing_note = ""
        if existing_terms:
            sample = list(existing_terms)[:50]
            existing_note = f"Already-handled terms (DO NOT include these): {', '.join(sample)}\n"

        system_content = (
            f"You are a terminology lead building a consistent glossary for a {source_lang}->{target_lang} translation project.\n"
            f"From the source text, extract up to {max_terms} HIGH-FREQUENCY, domain-specific / professional terms "
            f"(product names, technical terms, organization names, jargon). "
            f"EXCLUDE ordinary everyday words and generic function words.\n"
            f"{existing_note}"
            f"For EACH term decide ONE single authoritative {target_lang} translation to be used consistently everywhere.\n"
            f"Respond with ONLY a JSON object mapping the exact source term to its {target_lang} translation, e.g. "
            f'{{"源词A": "translationA", "源词B": "translationB"}}. No comments, no markdown, no extra text.'
        )

        payload = {
            "model": DEFAULT_CONFIG['MODEL_FAST'],
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": corpus}
            ],
            "temperature": 0.1,
            "max_tokens": DEFAULT_CONFIG['MAX_TOKENS_PROOFREAD'],
            "stream": False
        }

        for attempt in range(DEFAULT_CONFIG['API_RETRY_COUNT']):
            try:
                resp = self.session.post(
                    self.base_url,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=120
                )
                if resp.status_code in (429, 500, 502, 503):
                    time.sleep(DEFAULT_CONFIG['API_RETRY_DELAY'] * (2 ** attempt))
                    continue
                resp.raise_for_status()
                content = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '').strip()
                if not content:
                    return {}
                if content.startswith('```'):
                    content = re.sub(r'^```[a-zA-Z]*\s*', '', content)
                    content = re.sub(r'\s*```$', '', content)
                parsed = json.loads(content)
                result = {}
                if isinstance(parsed, dict):
                    for k, v in parsed.items():
                        ks, vs = str(k).strip(), str(v).strip()
                        if ks and vs and ks != vs:
                            result[ks] = vs
                return result
            except json.JSONDecodeError as e:
                print(f"Term arbitration JSON error: {e}")
                return {}
            except Exception as e:
                if attempt < DEFAULT_CONFIG['API_RETRY_COUNT'] - 1:
                    time.sleep(DEFAULT_CONFIG['API_RETRY_DELAY'] * (2 ** attempt))
                else:
                    print(f"Term arbitration error: {e}")
        return {}

    def proofread_batch(self, texts: List[str], language: str, options: Dict[str, bool]) -> List[Dict]:
        """
        批量校对文本，返回每段文本的问题列表
        options: {'grammar': True, 'natural': True, 'style': True}
        返回: [{'original': str, 'issues': [{'type': str, 'description': str, 'suggestion': str}], 'has_issues': bool}, ...]
        """
        if not texts:
            return []
        
        check_items = []
        if options.get('grammar', True):
            check_items.append("grammar errors (语法错误)")
        if options.get('natural', True):
            check_items.append("unnatural expressions (不自然的表达)")
        if options.get('style', True):
            check_items.append("style inconsistencies (风格不一致)")
        
        check_list = ", ".join(check_items) if check_items else "all issues"
        
        system_content = f"""You are a professional {language} proofreader and editor.
Your task is to review text and identify issues in: {check_list}.

For each text segment, analyze and respond in this EXACT JSON format:
{{"issues": [{{"type": "grammar|natural|style", "description": "问题描述", "suggestion": "建议修改"}}], "verdict": "ok|needs_review"}}

RULES:
1. If no issues found, return: {{"issues": [], "verdict": "ok"}}
2. Be specific about what's wrong and provide clear suggestions
3. Use bilingual descriptions (English + Chinese) for clarity
4. Focus only on real issues, not stylistic preferences
5. Input texts are separated by '{DEFAULT_CONFIG['TERM_SEPARATOR']}'
6. Output one JSON per text, separated by '{DEFAULT_CONFIG['TERM_SEPARATOR']}'
7. Number of outputs MUST match number of inputs"""

        user_content = DEFAULT_CONFIG['TERM_SEPARATOR'].join(texts)
        
        payload = {
            "model": DEFAULT_CONFIG['MODEL_FAST'],
            "messages": [
                {"role": "system", "content": system_content},
                {"role": "user", "content": f"Proofread these {language} texts:\n\n{user_content}"}
            ],
            "temperature": 0.3,
            "max_tokens": DEFAULT_CONFIG['MAX_TOKENS_PROOFREAD'],
            "stream": False
        }

        sep = DEFAULT_CONFIG['TERM_SEPARATOR']
        for attempt in range(DEFAULT_CONFIG['API_RETRY_COUNT']):
            try:
                resp = self.session.post(
                    self.base_url,
                    headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=180
                )
                if resp.status_code in (429, 500, 502, 503):
                    time.sleep(DEFAULT_CONFIG['API_RETRY_DELAY'] * (2 ** attempt))
                    continue
                resp.raise_for_status()
                content = resp.json().get('choices', [{}])[0].get('message', {}).get('content', '')

                if not content:
                    return [{'original': t, 'issues': [], 'has_issues': False} for t in texts]

                results = []
                segments = content.split(sep)

                for i, text in enumerate(texts):
                    result = {'original': text, 'issues': [], 'has_issues': False}
                    if i < len(segments):
                        try:
                            segment = segments[i].strip()
                            if segment.startswith('```'):
                                segment = re.sub(r'^```json?\s*', '', segment)
                                segment = re.sub(r'\s*```$', '', segment)
                            parsed = json.loads(segment)
                            result['issues'] = parsed.get('issues', [])
                            result['has_issues'] = len(result['issues']) > 0 or parsed.get('verdict') == 'needs_review'
                        except json.JSONDecodeError:
                            if 'error' in segment.lower() or 'issue' in segment.lower():
                                result['issues'] = [{'type': 'unknown', 'description': segment[:200], 'suggestion': ''}]
                                result['has_issues'] = True
                    results.append(result)

                return results

            except Exception as e:
                if attempt < DEFAULT_CONFIG['API_RETRY_COUNT'] - 1:
                    time.sleep(DEFAULT_CONFIG['API_RETRY_DELAY'] * (2 ** attempt))
                else:
                    print(f"Proofreading API error: {e}")
                    return [{'original': t, 'issues': [{'type': 'error', 'description': f'API error: {str(e)}', 'suggestion': ''}], 'has_issues': False} for t in texts]
        return [{'original': t, 'issues': [], 'has_issues': False} for t in texts]


def get_script_directory() -> str:
    """获取脚本所在目录（而非工作目录）"""
    try:
        # 尝试获取脚本文件所在目录
        script_path = os.path.abspath(__file__)
        return os.path.dirname(script_path)
    except NameError:
        # 如果__file__不可用，回退到工作目录
        return os.getcwd()


def scan_glossary_files() -> Dict[str, Dict]:
    """扫描术语文件夹，返回所有CSV文件信息"""
    global GLOSSARY_FILES
    
    script_dir = get_script_directory()
    terms_dir_path = os.path.join(script_dir, DEFAULT_CONFIG['TERMS_DIR'])
    
    print(f"📂 Scanning terms in: {terms_dir_path}")
    
    if not os.path.isdir(terms_dir_path):
        print(f"⚠️ No terms folder found")
        return {}
    
    csv_files = glob.glob(os.path.join(terms_dir_path, "*.csv"))
    if not csv_files:
        print(f"⚠️ No CSV files found")
        return {}
    
    file_info = {}
    
    for csv_file in csv_files:
        filename = os.path.basename(csv_file)
        try:
            df = None
            for encoding in ['utf-8', 'utf-8-sig', 'gbk', 'latin1']:
                try:
                    df = pd.read_csv(csv_file, encoding=encoding)
                    break
                except UnicodeDecodeError: 
                    continue
            
            if df is None:
                file_info[filename] = {
                    'enabled': False,
                    'terms': 0,
                    'path': csv_file,
                    'status': 'error',
                    'error': 'Cannot read file'
                }
                continue
            
            # 列名标准化
            df.columns = df.columns.str.strip().str.lower()
            
            # 尝试识别source列
            source_col = None
            for col_name in ['source', 'src', '原文', '源', 'original', 'from', '原词', 'term']:
                if col_name in df.columns:
                    source_col = col_name
                    break
            
            # 尝试识别target列
            target_col = None
            for col_name in ['target', 'tgt', '译文', '目标', 'translation', 'to', '译词', 'translated']:
                if col_name in df.columns:
                    target_col = col_name
                    break
            
            # 如果只有两列，假设第一列是source，第二列是target
            if source_col is None and target_col is None and len(df.columns) == 2:
                source_col = df.columns[0]
                target_col = df.columns[1]
            
            if source_col is None or target_col is None:
                file_info[filename] = {
                    'enabled': False,
                    'terms': 0,
                    'path': csv_file,
                    'status': 'error',
                    'error': f'Cannot identify columns: {list(df.columns)}'
                }
                continue
            
            df.dropna(subset=[source_col, target_col], inplace=True)
            term_count = len(df)
            
            # 保留之前的启用状态，如果存在的话
            prev_enabled = GLOSSARY_FILES.get(filename, {}).get('enabled', True)
            
            file_info[filename] = {
                'enabled': prev_enabled,
                'terms': term_count,
                'path': csv_file,
                'status': 'ok',
                'source_col': source_col,
                'target_col': target_col
            }
            
            print(f"📋 {filename}: {term_count} terms ({source_col}->{target_col})")
            
        except Exception as e:
            file_info[filename] = {
                'enabled': False,
                'terms': 0,
                'path': csv_file,
                'status': 'error',
                'error': str(e)
            }
    
    GLOSSARY_FILES = file_info
    return file_info


def load_terms_sync() -> Tuple[Dict[str, str], re.Pattern | None, str]:
    """同步加载术语表 - 只加载启用的文件"""
    global GLOSSARY_FILES
    
    term_map = {}
    status_message = ""
    
    # 如果还没扫描过文件，先扫描
    if not GLOSSARY_FILES:
        scan_glossary_files()
    
    if not GLOSSARY_FILES:
        return {}, None, "⚠️ No terms files found"
    
    loaded_files, total_terms = 0, 0
    file_details = []
    
    for filename, info in GLOSSARY_FILES.items():
        if not info.get('enabled', True):
            file_details.append(f"⏸️ {filename}: disabled")
            continue
            
        if info.get('status') != 'ok':
            file_details.append(f"❌ {filename}: {info.get('error', 'error')}")
            continue
        
        try:
            csv_file = info['path']
            df = None
            for encoding in ['utf-8', 'utf-8-sig', 'gbk', 'latin1']:
                try:
                    df = pd.read_csv(csv_file, encoding=encoding)
                    break
                except UnicodeDecodeError: 
                    continue
            
            if df is None:
                continue
            
            df.columns = df.columns.str.strip().str.lower()
            source_col = info.get('source_col')
            target_col = info.get('target_col')
            
            if not source_col or not target_col:
                continue
            
            df.dropna(subset=[source_col, target_col], inplace=True)
            file_term_count = 0
            
            for _, row in df.iterrows():
                source, target = str(row[source_col]).strip(), str(row[target_col]).strip()
                if not source: 
                    continue
                term_map[source] = target
                file_term_count += 1
            
            total_terms += file_term_count
            loaded_files += 1
            file_details.append(f"✅ {filename}: {file_term_count} terms")
            
        except Exception as e:
            file_details.append(f"❌ {filename}: {str(e)}")
    
    if loaded_files == 0:
        status_message = "⚠️ No enabled terms files"
    else:
        summary = f"✅ Loaded {loaded_files} files with {total_terms} terms"
        details = "\n".join(file_details)
        status_message = f"{summary}"
    
    term_re = re.compile('|'.join(map(re.escape, sorted(term_map.keys(), key=len, reverse=True)))) if term_map else None
    return term_map, term_re, status_message


def load_terms() -> Tuple[Dict[str, str], re.Pattern | None, str]:
    """运行时加载术语表"""
    global TERMS_STATUS, _background_loader
    
    if TERMS_STATUS:
        return TERMS_STATUS
    
    if _background_loader:
        try:
            result = _background_loader.get_terms(timeout=3)
            TERMS_STATUS = result
            return result
        except Exception as e:
            print(f"Failed to get terms from background loader: {e}")
    
    result = load_terms_sync()
    TERMS_STATUS = result
    return result


def protect_and_replace_terms(text: str, term_map: Dict[str, str], term_re: re.Pattern | None) -> str:
    """在翻译前，查找所有源术语，替换为目标术语，并用<dnt>标签包裹"""
    if not term_re or not term_map or not text: 
        return text
    
    # 查找并替换术语
    def replace_term(match):
        original = match.group(0)
        replacement = term_map.get(original)
        if replacement:
            print(f"🔄 Term replaced: '{original}' -> '{replacement}'")
            return f"<dnt>{replacement}</dnt>"
        return original
    
    result = term_re.sub(replace_term, text)
    return result


class PPTElementInfo:
    """PPT元素信息类 - 存储元素、文本和字号信息"""
    def __init__(self, element, text: str, font_size_pt: Optional[float] = None, element_type: str = "text"):
        self.element = element
        self.original_text = text
        self.font_size_pt = font_size_pt
        self.element_type = element_type  # text, notes, table, group
        self.translated_text = None
        self.adjusted_font_size = None


def get_font_size_from_run(run) -> Optional[float]:
    """从run中获取字号（磅）"""
    try:
        if hasattr(run, 'font') and run.font and run.font.size:
            return run.font.size.pt
    except:
        pass
    return None


def get_paragraph_font_size(para) -> Optional[float]:
    """从段落中获取主要字号"""
    sizes = []
    try:
        for run in para.runs:
            size = get_font_size_from_run(run)
            if size:
                sizes.append(size)
    except:
        pass
    
    if sizes:
        return max(set(sizes), key=sizes.count)  # 返回出现最多的字号
    return None


def calculate_bilingual_font_size(original_size_pt: float, source_lang: str, target_lang: str) -> Tuple[float, float]:
    """
    计算双语模式下的字号
    返回: (原文字号, 译文字号)
    - 原文保持不变或略微缩小
    - 译文使用较小字号以适应布局
    """
    if not original_size_pt:
        return (12.0, 10.0)  # 默认值
    
    # 双语模式下，两种文字都需要缩小以适应布局
    # 原文缩小到85%，译文缩小到75%
    if is_chinese_to_western(source_lang, target_lang):
        # 中文原文保持原大小，英文译文缩小
        source_size = original_size_pt * 0.90
        target_size = original_size_pt * 0.75
    else:
        # 其他情况，两者都略微缩小
        source_size = original_size_pt * 0.85
        target_size = original_size_pt * 0.80
    
    # 确保字号在合理范围内
    min_size = FONT_SIZE_CONFIG["min_font_size_pt"]
    max_size = FONT_SIZE_CONFIG["max_font_size_pt"]
    
    source_size = max(min_size, min(max_size, source_size))
    target_size = max(min_size, min(max_size, target_size))
    
    return (source_size, target_size)


def update_element_bilingual(element_info: PPTElementInfo, translated_text: str, source_lang: str, target_lang: str, separator: str = "\n"):
    """
    双语模式更新元素：保留原文 + 追加译文
    """
    element = element_info.element
    original_text = element_info.original_text
    original_font_size = element_info.font_size_pt
    
    # 计算双语字号
    source_size, target_size = calculate_bilingual_font_size(original_font_size, source_lang, target_lang)
    
    try:
        if hasattr(element, 'runs') and hasattr(element, 'text'):
            # 保存原始格式
            original_runs = []
            for run in element.runs:
                run_info = {
                    'font_name': None, 'font_size': None, 'bold': False,
                    'italic': False, 'underline': False, 'color': None
                }
                try:
                    if hasattr(run, 'font'):
                        font = run.font
                        run_info['font_name'] = getattr(font, 'name', None)
                        run_info['font_size'] = getattr(font, 'size', None)
                        run_info['bold'] = getattr(font, 'bold', False)
                        run_info['italic'] = getattr(font, 'italic', False)
                        run_info['underline'] = getattr(font, 'underline', False)
                        try:
                            if hasattr(font, 'color') and hasattr(font.color, 'rgb'):
                                run_info['color'] = font.color.rgb
                        except: pass
                except: pass
                original_runs.append(run_info)
            
            first_run_info = original_runs[0] if original_runs else {}
            
            # 清空段落
            element.clear()
            
            # 添加原文 run
            source_run = element.add_run()
            source_run.text = original_text
            
            # 应用原文格式（缩小字号）
            if hasattr(source_run, 'font'):
                try:
                    if first_run_info.get('font_name'):
                        source_run.font.name = first_run_info['font_name']
                except: pass
                try:
                    source_run.font.size = Pt(source_size)
                except: pass
                try:
                    source_run.font.bold = first_run_info.get('bold', False)
                except: pass
                try:
                    if first_run_info.get('color'):
                        source_run.font.color.rgb = first_run_info['color']
                except: pass
            
            # 添加分隔符 run
            sep_run = element.add_run()
            sep_run.text = separator
            
            # 添加译文 run
            target_run = element.add_run()
            target_run.text = translated_text
            
            # 应用译文格式（更小字号，可选斜体以区分）
            if hasattr(target_run, 'font'):
                try:
                    if first_run_info.get('font_name'):
                        target_run.font.name = first_run_info['font_name']
                except: pass
                try:
                    target_run.font.size = Pt(target_size)
                except: pass
                try:
                    target_run.font.italic = True  # 译文用斜体区分
                except: pass
                try:
                    # 译文使用稍浅的颜色
                    if first_run_info.get('color'):
                        target_run.font.color.rgb = first_run_info['color']
                except: pass
            
            element_info.adjusted_font_size = target_size
            
        elif hasattr(element, 'text'):
            # 简单文本属性 - 直接追加
            element.text = f"{original_text}{separator}{translated_text}"
            
    except Exception as e:
        print(f"Warning: Could not update element in bilingual mode: {e}")
        # 降级：直接追加文本
        try:
            if hasattr(element, 'text'):
                element.text = f"{original_text}{separator}{translated_text}"
        except: pass


def update_element_text_with_font_adjustment(element_info: PPTElementInfo, new_text: str, source_lang: str, target_lang: str):
    """
    更新元素文本，并根据需要调整字号
    """
    element = element_info.element
    original_text = element_info.original_text
    original_font_size = element_info.font_size_pt
    
    # 计算是否需要调整字号
    adjusted_size = None
    if original_font_size and is_chinese_to_western(source_lang, target_lang):
        adjusted_size = calculate_adjusted_font_size(
            original_font_size, source_lang, target_lang, original_text, new_text
        )
    
    try:
        if hasattr(element, 'runs') and hasattr(element, 'text'):
            # 段落对象
            original_runs = []
            for run in element.runs:
                run_info = {
                    'font_name': None,
                    'font_size': None,
                    'bold': False,
                    'italic': False,
                    'underline': False,
                    'color': None
                }
                try:
                    if hasattr(run, 'font'):
                        font = run.font
                        run_info['font_name'] = getattr(font, 'name', None)
                        run_info['font_size'] = getattr(font, 'size', None)
                        run_info['bold'] = getattr(font, 'bold', False)
                        run_info['italic'] = getattr(font, 'italic', False)
                        run_info['underline'] = getattr(font, 'underline', False)
                        try:
                            if hasattr(font, 'color') and hasattr(font.color, 'rgb'):
                                run_info['color'] = font.color.rgb
                        except:
                            pass
                except:
                    pass
                original_runs.append(run_info)
            
            # 清空并重新填充
            element.clear()
            new_run = element.add_run()
            new_run.text = new_text
            
            # 应用格式
            if original_runs and hasattr(new_run, 'font'):
                first_run = original_runs[0]
                try:
                    if first_run['font_name']:
                        new_run.font.name = first_run['font_name']
                except: pass
                
                # 应用调整后的字号或原始字号
                try:
                    if adjusted_size:
                        new_run.font.size = Pt(adjusted_size)
                        element_info.adjusted_font_size = adjusted_size
                    elif first_run['font_size']:
                        new_run.font.size = first_run['font_size']
                except: pass
                
                try:
                    new_run.font.bold = first_run['bold']
                except: pass
                try:
                    new_run.font.italic = first_run['italic']
                except: pass
                try:
                    new_run.font.underline = first_run['underline']
                except: pass
                try:
                    if first_run['color']:
                        new_run.font.color.rgb = first_run['color']
                except: pass
                        
        elif hasattr(element, 'text'):
            element.text = new_text
            
    except Exception as e:
        print(f"Warning: Could not update element text: {e}")
        try:
            if hasattr(element, 'text'):
                element.text = new_text
        except:
            pass


def update_element_text(element: Any, new_text: str):
    """统一的元素文本更新函数（非PPT元素）"""
    if isinstance(element, openpyxl.cell.cell.Cell):
        element.value = new_text
        return

    try:
        if hasattr(element, 'runs') and hasattr(element, 'text'):
            original_runs = []
            for run in element.runs:
                run_info = {
                    'font_name': None, 'font_size': None, 'bold': False,
                    'italic': False, 'underline': False, 'color': None
                }
                try:
                    if hasattr(run, 'font'):
                        font = run.font
                        run_info['font_name'] = getattr(font, 'name', None)
                        run_info['font_size'] = getattr(font, 'size', None)
                        run_info['bold'] = getattr(font, 'bold', False)
                        run_info['italic'] = getattr(font, 'italic', False)
                        run_info['underline'] = getattr(font, 'underline', False)
                        try:
                            if hasattr(font, 'color') and hasattr(font.color, 'rgb'):
                                run_info['color'] = font.color.rgb
                        except: pass
                except: pass
                original_runs.append(run_info)
            
            if isinstance(element, docx.text.paragraph.Paragraph):
                original_style = element.style
                original_alignment = element.alignment
                p = element._element
                p.clear_content()
                new_run = element.add_run(new_text)
                try:
                    element.style = original_style
                    if original_alignment is not None:
                        element.alignment = original_alignment
                except: pass
            else:
                element.clear()
                new_run = element.add_run()
                new_run.text = new_text
            
            if original_runs and hasattr(new_run, 'font'):
                first_run = original_runs[0]
                try:
                    if first_run['font_name']: new_run.font.name = first_run['font_name']
                except: pass
                try:
                    if first_run['font_size']: new_run.font.size = first_run['font_size']
                except: pass
                try: new_run.font.bold = first_run['bold']
                except: pass
                try: new_run.font.italic = first_run['italic']
                except: pass
                try: new_run.font.underline = first_run['underline']
                except: pass
                try:
                    if first_run['color']: new_run.font.color.rgb = first_run['color']
                except: pass
                        
        elif hasattr(element, 'text'):
            element.text = new_text
            
    except Exception as e:
        print(f"Warning: Could not update element text for {type(element)}: {e}")
        try:
            if hasattr(element, 'text'):
                element.text = new_text
        except: pass


def extract_from_group_shape(group_shape, elements: List[PPTElementInfo], context: str = ""):
    """
    递归提取Group形状内的所有文本元素
    """
    try:
        if not hasattr(group_shape, 'shapes'):
            return
        
        for shape in group_shape.shapes:
            try:
                # 如果是嵌套的Group，递归处理
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    extract_from_group_shape(shape, elements, f"{context}_nested_group")
                    continue
                
                # 处理文本框
                if hasattr(shape, 'has_text_frame') and shape.has_text_frame:
                    text_frame = shape.text_frame
                    if text_frame and hasattr(text_frame, 'paragraphs'):
                        for para in text_frame.paragraphs:
                            if para and hasattr(para, 'text') and para.text.strip():
                                font_size = get_paragraph_font_size(para)
                                elements.append(PPTElementInfo(
                                    para, para.text, font_size, "group"
                                ))
                
                # 处理表格
                if hasattr(shape, 'has_table') and shape.has_table:
                    for row in shape.table.rows:
                        for cell in row.cells:
                            if hasattr(cell, 'text_frame') and cell.text_frame:
                                for para in cell.text_frame.paragraphs:
                                    if para and hasattr(para, 'text') and para.text.strip():
                                        font_size = get_paragraph_font_size(para)
                                        elements.append(PPTElementInfo(
                                            para, para.text, font_size, "group_table"
                                        ))
            except Exception as e:
                print(f"Error extracting from shape in group: {e}")
                continue
                
    except Exception as e:
        print(f"Error extracting from group shape: {e}")


def extract_ppt_elements_enhanced(prs, elements: List[PPTElementInfo]):
    """
    增强版PPT元素提取 - 支持Group形状和Notes讲稿
    """
    
    def extract_from_shapes(shapes, context=""):
        """从形状集合中提取文本（包括Group）"""
        for shape in shapes:
            try:
                # 处理Group形状 - 递归提取
                if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
                    extract_from_group_shape(shape, elements, f"{context}_group")
                    continue
                
                # 处理普通文本框
                if hasattr(shape, 'has_text_frame') and shape.has_text_frame:
                    text_frame = shape.text_frame
                    if text_frame and hasattr(text_frame, 'paragraphs'):
                        for para in text_frame.paragraphs:
                            if para and hasattr(para, 'text') and para.text.strip():
                                font_size = get_paragraph_font_size(para)
                                elements.append(PPTElementInfo(
                                    para, para.text, font_size, "text"
                                ))
                
                # 处理表格
                if hasattr(shape, 'has_table') and shape.has_table:
                    for row in shape.table.rows:
                        for cell in row.cells:
                            if hasattr(cell, 'text_frame') and cell.text_frame:
                                for para in cell.text_frame.paragraphs:
                                    if para and hasattr(para, 'text') and para.text.strip():
                                        font_size = get_paragraph_font_size(para)
                                        elements.append(PPTElementInfo(
                                            para, para.text, font_size, "table"
                                        ))
                                        
            except Exception as e:
                continue
    
    # 1. 从普通幻灯片提取
    if hasattr(prs, 'slides'):
        for slide_idx, slide in enumerate(prs.slides):
            try:
                # 从形状中提取（包括Group）
                extract_from_shapes(slide.shapes, f"slide_{slide_idx}")
                
                # 从备注/讲稿中提取 - 增强版
                try:
                    if hasattr(slide, 'has_notes_slide') and slide.has_notes_slide:
                        notes_slide = slide.notes_slide
                        if notes_slide:
                            # 方法1: 直接从notes_text_frame提取
                            if hasattr(notes_slide, 'notes_text_frame') and notes_slide.notes_text_frame:
                                for para in notes_slide.notes_text_frame.paragraphs:
                                    if para and hasattr(para, 'text') and para.text.strip():
                                        font_size = get_paragraph_font_size(para)
                                        elements.append(PPTElementInfo(
                                            para, para.text, font_size, "notes"
                                        ))
                            
                            # 方法2: 从shapes中提取（某些PPT结构可能需要这种方式）
                            if hasattr(notes_slide, 'shapes'):
                                for shape in notes_slide.shapes:
                                    # 跳过幻灯片缩略图占位符
                                    if hasattr(shape, 'placeholder_format'):
                                        ph_type = shape.placeholder_format.type
                                        # 跳过SLIDE_IMAGE类型
                                        if ph_type and str(ph_type) == 'SLIDE_IMAGE (12)':
                                            continue
                                    
                                    if hasattr(shape, 'has_text_frame') and shape.has_text_frame:
                                        for para in shape.text_frame.paragraphs:
                                            text = para.text.strip() if para and hasattr(para, 'text') else ""
                                            # 避免重复添加
                                            if text and not any(e.original_text == text and e.element_type == "notes" for e in elements):
                                                font_size = get_paragraph_font_size(para)
                                                elements.append(PPTElementInfo(
                                                    para, text, font_size, "notes"
                                                ))
                except Exception as e:
                    print(f"Error extracting notes from slide {slide_idx}: {e}")
                    
            except Exception as e:
                continue
    
    # 2. 从幻灯片母版提取
    try:
        if hasattr(prs, 'slide_master') and prs.slide_master:
            extract_from_shapes(prs.slide_master.shapes, "slide_master")
    except Exception:
        pass
    
    # 3. 从布局母版提取
    try:
        if hasattr(prs, 'slide_layouts'):
            for layout_idx, layout in enumerate(prs.slide_layouts):
                try:
                    if layout and hasattr(layout, 'shapes'):
                        extract_from_shapes(layout.shapes, f"layout_{layout_idx}")
                except Exception:
                    continue
    except Exception:
        pass


def maybe_arbitrate_terms(source_texts, term_map, term_re, translator,
                          source_lang, target_lang, adv, progress_callback):
    """
    可选「译法仲裁」：抽取高频专业词并裁定唯一译法，合并进术语表，
    之后由现有 <dnt> 机制在全文强制使用同一译法。
    小语种目标按中转语言（英文）裁定，以与中转阶段保持一致。
    返回更新后的 (term_map, term_re)。
    """
    adv = adv or {}
    if not adv.get('termArbitration'):
        return term_map, term_re
    try:
        use_pivot = adv.get('pivot') and is_minor_language_target(source_lang, target_lang)
        arb_target = DEFAULT_CONFIG.get('PIVOT_LANGUAGE', 'English') if use_pivot else target_lang
        progress_callback.status(f"Arbitrating term translations ({source_lang}→{arb_target})... | 仲裁专业词译法...")
        existing = set(term_map.keys()) if term_map else set()
        arbitrated = translator.arbitrate_terms(source_texts, source_lang, arb_target, existing)
        if not arbitrated:
            progress_callback.info("Term arbitration: no new terms | 译法仲裁：未发现新增专业词")
            return term_map, term_re
        merged = dict(term_map) if term_map else {}
        added = 0
        for k, v in arbitrated.items():
            if k and k not in merged:
                merged[k] = v
                added += 1
        new_re = re.compile('|'.join(map(re.escape, sorted(merged.keys(), key=len, reverse=True)))) if merged else None
        progress_callback.info(f"Term arbitration: +{added} unified terms | 译法仲裁：统一{added}个专业词")
        return merged, new_re
    except Exception as e:
        print(f"Term arbitration failed: {e}")
        return term_map, term_re


def translate_unique_texts(translator, unique_texts, source_lang, target_lang,
                           formality, adv, progress_callback, need_capitalize):
    """
    统一翻译入口，封装三项高级能力：
      - 机密模式：乱序 + 更小批次，让任一次 API 请求都拿不到连贯原文；
      - 脑补检查：把整页情景作为上下文随请求下发；
      - 小语种英文中转：中文 -> 英文 -> 目标语种两段式翻译。
    入参 unique_texts 为「已做术语保护」的文本，返回 {protected_text: translated_text}。
    （TM 写回由调用方按原文键处理，以保证下次按原文命中。）
    """
    adv = adv or {}
    result_map: Dict[str, str] = {}
    if not unique_texts:
        return result_map

    confidential = bool(adv.get('confidential'))
    context_hint = adv.get('contextHint', '') if adv.get('contextInference') else ''
    use_pivot = bool(adv.get('pivot')) and is_minor_language_target(source_lang, target_lang)
    pivot_lang = DEFAULT_CONFIG.get('PIVOT_LANGUAGE', 'English')
    batch_size = DEFAULT_CONFIG['CONFIDENTIAL_BATCH_SIZE'] if confidential else DEFAULT_CONFIG['BATCH_SIZE']

    work = list(unique_texts)
    if confidential:
        random.shuffle(work)  # 切碎并打乱顺序，保护机密

    def run_stage(texts, s_lang, t_lang, hint, capitalize):
        out: Dict[str, str] = {}
        if not texts:
            return out
        batches = [texts[i:i + batch_size] for i in range(0, len(texts), batch_size)]
        with ThreadPoolExecutor(max_workers=6) as executor:
            fut = {executor.submit(translator.translate_batch, b, s_lang, t_lang, formality, hint): b for b in batches}
            for i, future in enumerate(as_completed(fut)):
                progress_callback.progress(f"Processing batch {i+1}/{len(batches)} ({s_lang}→{t_lang})...")
                try:
                    ob = fut[future]
                    tb = future.result()
                    if capitalize:
                        tb = [capitalize_first_letter(x) for x in tb]
                    out.update(zip(ob, tb))
                except Exception as e:
                    print(f"Error in translation batch: {e}")
                    progress_callback.error(f"Batch translation error: {str(e)}")
        return out

    if use_pivot:
        progress_callback.info(f"Pivot via {pivot_lang} | 经{pivot_lang}中转: {source_lang}→{pivot_lang}→{target_lang}")
        # 阶段1：源语种 -> 英文（保留 glossary 注入的英文术语）
        en_map = run_stage(work, source_lang, pivot_lang, context_hint, False)
        # 阶段2：英文 -> 目标小语种
        en_texts = sorted({v for v in en_map.values() if v and v.strip()}, key=len)
        en_to_target = run_stage(en_texts, pivot_lang, target_lang, '', need_capitalize)
        for orig in work:
            en = en_map.get(orig, orig)
            result_map[orig] = en_to_target.get(en, en)
    else:
        result_map = run_stage(work, source_lang, target_lang, context_hint, need_capitalize)

    return result_map


def process_ppt_elements(elements: List[PPTElementInfo], term_map, term_re, translator,
                         source_lang, target_lang, progress_callback, translation_mode: str = "replace",
                         formality: str = "auto", adv: Optional[Dict] = None):
    """
    处理PPT元素 - 支持字号调整、双语模式、翻译记忆
    translation_mode: 'replace' (替换原文) 或 'append' (双语对照)
    formality: 'formal' | 'informal' | 'auto'
    """
    if not elements:
        progress_callback.error("No text elements found | 未找到文本元素")
        return

    adv = adv or {}
    is_bilingual = translation_mode == "append"
    mode_text = "Bilingual | 双语" if is_bilingual else "Replace | 替换"
    tm = get_tm()

    type_counts: Dict[str, int] = {}
    for elem in elements:
        type_counts[elem.element_type] = type_counts.get(elem.element_type, 0) + 1

    type_summary = ", ".join([f"{k}: {v}" for k, v in type_counts.items()])
    progress_callback.progress(f"Found {len(elements)} elements ({type_summary}) [{mode_text}]")

    need_capitalize = is_chinese_to_western(source_lang, target_lang)

    # 可选译法仲裁：统一高频专业词译法
    source_texts_all = [e.original_text for e in elements if e.original_text]
    term_map, term_re = maybe_arbitrate_terms(source_texts_all, term_map, term_re, translator,
                                              source_lang, target_lang, adv, progress_callback)

    progress_callback.status("Analyzing text, checking TM, protecting terms... | 分析文本、查询TM、保护术语...")

    texts_to_translate = []
    skipped_count = 0
    tm_hit_count = 0
    tm_hit_map: Dict[str, str] = {}            # protected_text -> TM translation
    protected_to_source: Dict[str, str] = {}   # protected_text -> raw original (for TM write-back)

    for elem_info in elements:
        original_text = elem_info.original_text

        if is_text_already_target_language(original_text, target_lang):
            skipped_count += 1
            continue

        segments = detect_mixed_language_segments(original_text, source_lang)
        needs_translation = any(needs_trans for _, needs_trans in segments)

        if needs_translation:
            protected_text = protect_and_replace_terms(original_text, term_map, term_re)
            protected_to_source[protected_text] = original_text
            # Check TM first (exact then fuzzy/normalized) by RAW source text
            if tm:
                tm_result = tm.lookup(original_text, source_lang, target_lang, fuzzy=adv.get('fuzzyTM', True))
                if tm_result:
                    tm_hit_map[protected_text] = tm_result
                    tm_hit_count += 1
                    continue
            texts_to_translate.append(protected_text)

    if skipped_count > 0:
        progress_callback.info(f"Skipped {skipped_count} (already target lang) | 跳过{skipped_count}个已是目标语言")
    if tm_hit_count > 0:
        progress_callback.info(f"TM hits: {tm_hit_count} segments from memory | TM命中: {tm_hit_count}个来自记忆库")

    if not texts_to_translate and not tm_hit_map:
        progress_callback.status("No text requires translation | 没有文本需要翻译")
        return

    progress_callback.progress(f"Translating {len(texts_to_translate)} new segments (+ {tm_hit_count} from TM) | 翻译{len(texts_to_translate)}个新段落")

    # 批量翻译（仅未命中TM的），统一走机密/脑补/中转处理
    unique_texts = sorted(list(set(texts_to_translate)), key=len)
    api_translation_map: Dict[str, str] = {}

    if unique_texts:
        progress_callback.progress(f"Translating {len(unique_texts)} unique segments... | 正在翻译{len(unique_texts)}个独特片段...")
        api_translation_map = translate_unique_texts(
            translator, unique_texts, source_lang, target_lang,
            formality, adv, progress_callback, need_capitalize
        )
        # 按「原文」键写回 TM，保证下次按原文命中
        if tm:
            tm_pairs = [(protected_to_source.get(p, p), clean_dnt_tags(t))
                        for p, t in api_translation_map.items()]
            tm.store_batch(tm_pairs, source_lang, target_lang)

    # 回填结果并调整字号
    progress_callback.status("Updating document with layout adjustments... | 更新文档并调整布局...")

    updated_count = 0
    font_adjusted_count = 0

    for elem_info in elements:
        if is_text_already_target_language(elem_info.original_text, target_lang):
            continue

        segments = detect_mixed_language_segments(elem_info.original_text, source_lang)
        needs_translation = any(needs_trans for _, needs_trans in segments)

        if needs_translation:
            protected_text = protect_and_replace_terms(elem_info.original_text, term_map, term_re)
            # Prefer TM hit, then API result, then original
            final_text = tm_hit_map.get(protected_text) or api_translation_map.get(protected_text, elem_info.original_text)
            final_text = clean_dnt_tags(final_text)
            elem_info.translated_text = final_text

            if elem_info.original_text != final_text:
                try:
                    if is_bilingual:
                        update_element_bilingual(elem_info, final_text, source_lang, target_lang, separator="\n")
                    else:
                        update_element_text_with_font_adjustment(elem_info, final_text, source_lang, target_lang)
                    updated_count += 1
                    if elem_info.adjusted_font_size:
                        font_adjusted_count += 1
                except Exception as e:
                    print(f"Error updating element: {e}")
                    progress_callback.error(f"Error updating text: {str(e)}")

    summary = f"Updated {updated_count} elements"
    if tm_hit_count > 0:
        summary += f", {tm_hit_count} from TM"
    if skipped_count > 0:
        summary += f", skipped {skipped_count}"
    if is_bilingual:
        summary += " (bilingual)"
    if font_adjusted_count > 0:
        summary += f", adjusted font for {font_adjusted_count}"
    progress_callback.progress(summary)


def process_text_elements(elements, term_map, term_re, translator, source_lang, target_lang,
                          progress_callback, translation_mode: str = "replace", formality: str = "auto",
                          adv: Optional[Dict] = None):
    """处理非PPT元素的文本（Word, Excel）- 支持双语模式、翻译记忆、机密/脑补/小语种中转/译法仲裁"""
    if not elements:
        progress_callback.error("No text elements found | 未找到文本元素")
        return

    adv = adv or {}
    is_bilingual = translation_mode == "append"
    mode_text = "Bilingual | 双语" if is_bilingual else "Replace | 替换"
    tm = get_tm()

    progress_callback.progress(f"Found {len(elements)} text elements [{mode_text}] | 找到{len(elements)}个文本元素")

    need_capitalize = is_chinese_to_western(source_lang, target_lang)

    # 可选译法仲裁：统一高频专业词译法
    source_texts_all = [t for _, t in elements if t]
    term_map, term_re = maybe_arbitrate_terms(source_texts_all, term_map, term_re, translator,
                                              source_lang, target_lang, adv, progress_callback)

    progress_callback.status("Analyzing text, checking TM, protecting terms... | 分析文本、查询TM、保护术语...")

    texts_to_translate = []
    skipped_count = 0
    tm_hit_count = 0
    tm_hit_map: Dict[str, str] = {}
    protected_to_source: Dict[str, str] = {}

    for element, original_text in elements:
        if is_text_already_target_language(original_text, target_lang):
            skipped_count += 1
            continue

        segments = detect_mixed_language_segments(original_text, source_lang)
        needs_translation = any(needs_trans for _, needs_trans in segments)

        if needs_translation:
            protected_text = protect_and_replace_terms(original_text, term_map, term_re)
            protected_to_source[protected_text] = original_text
            if tm:
                tm_result = tm.lookup(original_text, source_lang, target_lang, fuzzy=adv.get('fuzzyTM', True))
                if tm_result:
                    tm_hit_map[protected_text] = tm_result
                    tm_hit_count += 1
                    continue
            texts_to_translate.append(protected_text)

    if skipped_count > 0:
        progress_callback.info(f"Skipped {skipped_count} (already target lang) | 跳过{skipped_count}个已是目标语言")
    if tm_hit_count > 0:
        progress_callback.info(f"TM hits: {tm_hit_count} segments | TM命中{tm_hit_count}个段落")

    if not texts_to_translate and not tm_hit_map:
        progress_callback.status("No text requires translation | 没有文本需要翻译")
        return

    unique_texts_for_api = sorted(list(set(texts_to_translate)), key=len)
    api_translation_map: Dict[str, str] = {}

    if unique_texts_for_api:
        progress_callback.progress(f"Translating {len(unique_texts_for_api)} unique segments... | 正在翻译{len(unique_texts_for_api)}个独特片段...")
        api_translation_map = translate_unique_texts(
            translator, unique_texts_for_api, source_lang, target_lang,
            formality, adv, progress_callback, need_capitalize
        )
        if tm:
            tm_pairs = [(protected_to_source.get(p, p), clean_dnt_tags(t))
                        for p, t in api_translation_map.items()]
            tm.store_batch(tm_pairs, source_lang, target_lang)

    progress_callback.status("Updating document... | 更新文档...")

    updated_count = 0
    for element, original_text in elements:
        if is_text_already_target_language(original_text, target_lang):
            continue

        segments = detect_mixed_language_segments(original_text, source_lang)
        needs_translation = any(needs_trans for _, needs_trans in segments)

        if needs_translation:
            protected_text = protect_and_replace_terms(original_text, term_map, term_re)
            final_text = tm_hit_map.get(protected_text) or api_translation_map.get(protected_text, original_text)
            final_text = clean_dnt_tags(final_text)

            if original_text != final_text:
                try:
                    if is_bilingual:
                        update_element_text(element, f"{original_text}\n{final_text}")
                    else:
                        update_element_text(element, final_text)
                    updated_count += 1
                except Exception as e:
                    print(f"Error updating element: {e}")
                    progress_callback.error(f"Error updating text: {str(e)}")

    summary = f"Successfully updated {updated_count} elements"
    if tm_hit_count > 0:
        summary += f", {tm_hit_count} from TM"
    if skipped_count > 0:
        summary += f", skipped {skipped_count}"
    if is_bilingual:
        summary += " (bilingual)"
    progress_callback.progress(summary)


def _predict_target_language_helper() -> str:
    """根据时区预测目标语言"""
    try:
        tz_name = datetime.datetime.now(datetime.timezone.utc).astimezone().tzname()
        iana_name = WINDOWS_TO_IANA_MAP.get(tz_name, tz_name)
        tz_region = pytz.timezone(iana_name).zone.split('/')[0]
        return TIMEZONE_TO_LANGUAGE_MAP.get(tz_region, DEFAULT_TARGET_LANGUAGE)
    except Exception: 
        return DEFAULT_TARGET_LANGUAGE


def get_inspirational_quote(api_key: str) -> str:
    """获取励志诗句"""
    if not api_key:
        quotes = [
            "山重水复疑无路，柳暗花明又一村！",
            "宝剑锋从磨砺出，梅花香自苦寒来！", 
            "千磨万击还坚劲，任尔东西南北风！",
            "海内存知己，天涯若比邻！"
        ]
        return random.choice(quotes)
        
    try:
        response = requests.post("https://api.deepseek.com/v1/chat/completions", 
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}, 
            json={"model": DEFAULT_CONFIG['MODEL_FAST'], "messages": [
                {"role": "system", "content": "You are a poetry expert."},
                {"role": "user", "content": "请用中文写一句意境优美、积极向上的中国古诗词，仅输出诗词本身，不要任何其他解释或标点符号。"}
            ], "temperature": 1.2, "max_tokens": 100}, timeout=8)
        response.raise_for_status()
        return response.json()['choices'][0]['message']['content'].strip() + "！"
    except Exception:
        return random.choice(["山重水复疑无路，柳暗花明又一村！", "宝剑锋从磨砺出，梅花香自苦寒来！"])


def sample_text_for_language_detection(file_path: str) -> List[str]:
    """从文档中随机抽取文本用于语言检测"""
    try:
        ext, all_texts = os.path.splitext(file_path)[1].lower(), []
        limit = 30
        if ext in ('.xlsx', '.xls'):
            wb = openpyxl.load_workbook(file_path, read_only=True)
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        if cell.value and isinstance(cell.value, str) and cell.value.strip(): 
                            all_texts.append(cell.value.strip())
                        if len(all_texts) >= limit: break
                    if len(all_texts) >= limit: break
                if len(all_texts) >= limit: break
        elif ext == '.docx':
            doc = docx.Document(file_path)
            for para in doc.paragraphs:
                if para.text.strip(): 
                    all_texts.append(para.text.strip())
                if len(all_texts) >= limit: break
        elif ext == '.pptx':
            prs = Presentation(file_path)
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            if para.text.strip(): 
                                all_texts.append(para.text.strip())
                            if len(all_texts) >= limit: break
                        if len(all_texts) >= limit: break
                if len(all_texts) >= limit: break
        if not all_texts: return []
        sample_count = min(random.randint(5, 7), len(all_texts))
        return random.sample(all_texts, sample_count)
    except Exception as e:
        print(f"Error sampling text: {e}")
        return []


def parse_excel_exclude_rules(exclude_rule: str) -> Dict:
    """解析Excel排除规则"""
    if not exclude_rule: return {}
    excluded_cells, excluded_rows, excluded_cols = set(), set(), set()
    for rule in [r.strip().upper() for r in exclude_rule.split('/') if r.strip()]:
        try:
            if re.match(r'^[A-Z]+\d+$', rule): excluded_cells.add(rule)
            elif rule.isdigit(): excluded_rows.add(int(rule))
            elif ':' in rule:
                start_col, end_col = rule.split(':')
                if start_col.isalpha() and end_col.isalpha():
                    excluded_cols.update(range(openpyxl.utils.column_index_from_string(start_col), openpyxl.utils.column_index_from_string(end_col) + 1))
            elif rule.isalpha(): excluded_cols.add(openpyxl.utils.column_index_from_string(rule))
        except Exception: continue
    return {'cells': excluded_cells, 'rows': excluded_rows, 'cols': excluded_cols}


def should_exclude_excel_cell(cell, exclude_settings: Dict) -> bool:
    """判断Excel单元格是否应该被排除翻译"""
    if not exclude_settings: return False
    return cell.coordinate in exclude_settings.get('cells', set()) or cell.row in exclude_settings.get('rows', set()) or cell.column in exclude_settings.get('cols', set())


def check_file_accessibility(file_path: str) -> Tuple[bool, str]:
    """检查文件是否可访问"""
    try:
        if not os.path.exists(file_path): return False, "File does not exist"
        if not os.path.isfile(file_path): return False, "Path is not a file"
        filename = os.path.basename(file_path)
        if filename.startswith('~$') or filename.startswith('.tmp'): return False, "Temporary file detected"
        with open(file_path, 'rb') as f: f.read(1)
        return True, "File accessible"
    except PermissionError: return False, "File is in use by another process"
    except Exception as e: return False, f"File access error: {str(e)}"


def run_translation_process(settings, progress_callback, is_batch=False, file_index=0, total_files=0):
    """主翻译流程"""
    global GLOSSARY_ENABLED

    file_path = settings.get('filePath') if not is_batch else settings.get('currentFile')
    source_lang, target_lang, api_key = settings.get('sourceLang'), settings.get('targetLang'), settings.get('apiKey')
    translation_mode = settings.get('translationMode', 'replace')
    glossary_enabled = settings.get('glossaryEnabled', GLOSSARY_ENABLED)
    formality = settings.get('formality', 'auto')  # 'formal' | 'informal' | 'auto'

    # 高级选项：机密切碎 / 模糊TM / 小语种英文中转 / 脑补检查 / 译法仲裁
    adv = {
        'confidential': settings.get('confidentialMode', True),
        'fuzzyTM': settings.get('fuzzyTM', True),
        'pivot': settings.get('pivotThroughEnglish', True),
        'contextInference': settings.get('contextInference', False),
        'termArbitration': settings.get('termArbitration', False),
        'contextHint': '',
    }

    if not all([file_path, source_lang, target_lang, api_key]):
        progress_callback.error("Error: Missing settings | 错误: 缺少设置")
        return False

    accessible, access_msg = check_file_accessibility(file_path)
    if not accessible:
        progress_callback.error(f"File access error: {access_msg}")
        return False

    try:
        if is_batch:
            progress_callback.batch_progress(file_index + 1, total_files, os.path.basename(file_path))

        formality_label = {'formal': 'Formal | 正式', 'informal': 'Informal | 非正式'}.get(formality, 'Auto | 自动')
        mode_text = "Bilingual | 双语" if translation_mode == "append" else "Replace | 替换"

        if glossary_enabled:
            progress_callback.status(f"Mode: {mode_text} | Formality: {formality_label} | Loading terms...")
            term_map, term_re, terms_status = load_terms()
            progress_callback.info(terms_status)
        else:
            progress_callback.status(f"Mode: {mode_text} | Formality: {formality_label} | Glossary off")
            term_map, term_re = {}, None
            progress_callback.info("📚 Glossary disabled | 术语表已禁用")

        translator = DeepSeekTranslator(api_key, settings.get('temperature', 1.0))
        
        ext = os.path.splitext(file_path)[1].lower()
        
        # 根据翻译模式修改输出文件后缀
        mode_suffix = "--bilingual" if translation_mode == "append" else DEFAULT_CONFIG['OUTPUT_SUFFIX']
        output_path = f"{os.path.splitext(file_path)[0]}{mode_suffix}{ext}"
        doc_obj = None
        
        progress_callback.status(f"Extracting from {os.path.basename(file_path)}... | 从{os.path.basename(file_path)}提取内容...")
        
        if ext in ('.xlsx', '.xls'):
            elements = []
            wb = openpyxl.load_workbook(file_path)
            exclude_rule = settings.get('excelExclude', '') if not is_batch else settings.get('batchExcelExclude', '')
            exclude_settings = parse_excel_exclude_rules(exclude_rule)
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        if (cell.value and isinstance(cell.value, str) and cell.value.strip() and not should_exclude_excel_cell(cell, exclude_settings)):
                            elements.append((cell, cell.value))
            doc_obj = wb
            
            if elements:
                if adv.get('contextInference'):
                    adv['contextHint'] = build_context_hint([t for _, t in elements])
                progress_callback.progress(f"Starting translation of {len(elements)} elements...")
                process_text_elements(elements, term_map, term_re, translator, source_lang, target_lang, progress_callback, translation_mode, formality, adv)
                progress_callback.status("Saving translated document...")
                doc_obj.save(output_path)
            else:
                progress_callback.status("No text found to translate")

        elif ext == '.docx':
            elements = []
            doc = docx.Document(file_path)

            def get_elements_from_container(container, context=""):
                if container is None:
                    return
                if hasattr(container, 'paragraphs'):
                    for para in container.paragraphs:
                        if para and hasattr(para, 'text') and para.text.strip():
                            elements.append((para, para.text))
                if hasattr(container, 'tables'):
                    for table in container.tables:
                        for row in table.rows:
                            for cell in row.cells:
                                get_elements_from_container(cell, f"{context}_table_cell")

            get_elements_from_container(doc, "main_document")
            for section_idx, section in enumerate(doc.sections):
                get_elements_from_container(section.header, f"header_{section_idx}")
                get_elements_from_container(section.footer, f"footer_{section_idx}")

            doc_obj = doc

            if elements:
                if adv.get('contextInference'):
                    adv['contextHint'] = build_context_hint([t for _, t in elements])
                progress_callback.progress(f"Starting translation of {len(elements)} elements...")
                process_text_elements(elements, term_map, term_re, translator, source_lang, target_lang, progress_callback, translation_mode, formality, adv)
                progress_callback.status("Saving translated document...")
                doc_obj.save(output_path)
            else:
                progress_callback.status("No text found to translate")

        elif ext == '.pptx':
            ppt_elements: List[PPTElementInfo] = []
            try:
                prs = Presentation(file_path)
            except Exception:
                progress_callback.error(f"Cannot read '{os.path.basename(file_path)}': file is corrupted or not a valid PowerPoint (.pptx) file | 文件损坏或不是有效的PowerPoint文件")
                return False
            extract_ppt_elements_enhanced(prs, ppt_elements)
            doc_obj = prs

            if ppt_elements:
                if adv.get('contextInference'):
                    adv['contextHint'] = build_context_hint([e.original_text for e in ppt_elements])
                progress_callback.progress(f"Starting translation of {len(ppt_elements)} elements...")
                process_ppt_elements(ppt_elements, term_map, term_re, translator, source_lang, target_lang, progress_callback, translation_mode, formality, adv)
                progress_callback.status("Saving translated document...")
                doc_obj.save(output_path)
            else:
                progress_callback.status("No text found to translate")
            
        else:
            progress_callback.error(f"Error: Unsupported file type '{ext}'")
            return False
        
        if not is_batch:
            progress_callback.status(f"Translation completed! Saved as: {os.path.basename(output_path)}")
        
        return True
        
    except Exception as e:
        import traceback
        error_msg = f"Error processing {os.path.basename(file_path)}: {str(e)}"
        progress_callback.error(error_msg)
        print(traceback.format_exc())
        return False


def run_batch_translation(settings, progress_callback):
    """批量翻译处理"""
    file_paths = settings.get('filePaths', [])
    if not file_paths:
        progress_callback.error("No files selected for batch processing")
        return
    
    file_list = "Selected files:\n" + "\n".join([f"• {os.path.basename(fp)}" for fp in file_paths])
    progress_callback.info(file_list)
    
    successful_files, total_files, processed_files = 0, len(file_paths), []
    
    for i, file_path in enumerate(file_paths):
        settings['currentFile'] = file_path
        if run_translation_process(settings, progress_callback, is_batch=True, file_index=i, total_files=total_files):
            successful_files += 1
            output_name = f"{os.path.splitext(os.path.basename(file_path))[0]}--OP{os.path.splitext(file_path)[1]}"
            processed_files.append(output_name)
        # 通知前端：这张文档牌可以飞向角落了（失败的也要离场，避免牌堆卡住）
        progress_callback.file_done(os.path.basename(file_path))
    
    if processed_files:
        completed_list = "Completed files:\n" + "\n".join([f"✓ {fp}" for fp in processed_files])
        progress_callback.info(completed_list)
        
    quote = get_inspirational_quote(settings.get('apiKey'))
    completion_message = f"Batch translation complete! {successful_files}/{total_files} files processed successfully.\\n\\n{quote}"
    
    if WINDOW:
        safe_message = completion_message.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"')
        WINDOW.evaluate_js(f'window.showCompletion("{safe_message}")')


def run_proofreading_process(settings, progress_callback):
    """校对处理流程"""
    file_path = settings.get('filePath')
    language = settings.get('proofreadLang', 'English')
    api_key = settings.get('apiKey')
    options = settings.get('proofreadOptions', {'grammar': True, 'natural': True, 'style': True})
    
    if not file_path or not api_key:
        progress_callback.error("Error: Missing file or API key")
        return False
    
    accessible, access_msg = check_file_accessibility(file_path)
    if not accessible:
        progress_callback.error(f"File access error: {access_msg}")
        return False
    
    try:
        progress_callback.status(f"Starting proofreading ({language})... | 开始校对...")
        
        proofreader = DeepSeekTranslator(api_key, 0.3)
        ext = os.path.splitext(file_path)[1].lower()
        output_path = f"{os.path.splitext(file_path)[0]}--proofread{ext}"
        
        # 提取文本元素
        progress_callback.progress("Extracting text elements... | 提取文本元素...")
        
        elements = []  # [(element, text), ...]
        doc_obj = None
        
        if ext in ('.xlsx', '.xls'):
            wb = openpyxl.load_workbook(file_path)
            for ws in wb.worksheets:
                for row in ws.iter_rows():
                    for cell in row:
                        if cell.value and isinstance(cell.value, str) and cell.value.strip():
                            elements.append((cell, cell.value, 'excel'))
            doc_obj = wb
            
        elif ext == '.docx':
            doc = docx.Document(file_path)
            for para in doc.paragraphs:
                if para and para.text.strip():
                    elements.append((para, para.text, 'docx'))
            for table in doc.tables:
                for row in table.rows:
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            if para and para.text.strip():
                                elements.append((para, para.text, 'docx_table'))
            doc_obj = doc
            
        elif ext == '.pptx':
            try:
                prs = Presentation(file_path)
            except Exception:
                progress_callback.error(f"Cannot read '{os.path.basename(file_path)}': file is corrupted or not a valid PowerPoint (.pptx) file | 文件损坏或不是有效的PowerPoint文件")
                return False
            for slide in prs.slides:
                for shape in slide.shapes:
                    if hasattr(shape, 'has_text_frame') and shape.has_text_frame:
                        for para in shape.text_frame.paragraphs:
                            if para and para.text.strip():
                                elements.append((para, para.text, 'pptx'))
                    if hasattr(shape, 'has_table') and shape.has_table:
                        for row in shape.table.rows:
                            for cell in row.cells:
                                for para in cell.text_frame.paragraphs:
                                    if para and para.text.strip():
                                        elements.append((para, para.text, 'pptx_table'))
                # 检查备注
                if hasattr(slide, 'has_notes_slide') and slide.has_notes_slide:
                    notes = slide.notes_slide
                    if hasattr(notes, 'notes_text_frame') and notes.notes_text_frame:
                        for para in notes.notes_text_frame.paragraphs:
                            if para and para.text.strip():
                                elements.append((para, para.text, 'pptx_notes'))
            doc_obj = prs
        else:
            progress_callback.error(f"Unsupported file type: {ext}")
            return False
        
        if not elements:
            progress_callback.status("No text found to proofread | 未找到要校对的文本")
            return False
        
        progress_callback.progress(f"Found {len(elements)} text elements | 发现{len(elements)}个文本元素")
        
        # 批量校对
        texts = [text for _, text, _ in elements]
        unique_texts = list(set(texts))
        
        progress_callback.progress(f"Proofreading {len(unique_texts)} unique segments... | 校对{len(unique_texts)}个独特片段...")
        
        # 分批处理
        batch_size = 15  # 校对用较小的批次
        all_results = {}
        
        for i in range(0, len(unique_texts), batch_size):
            batch = unique_texts[i:i+batch_size]
            progress_callback.progress(f"Processing batch {i//batch_size + 1}/{(len(unique_texts) + batch_size - 1)//batch_size}...")
            
            results = proofreader.proofread_batch(batch, language, options)
            for text, result in zip(batch, results):
                all_results[text] = result
        
        # 统计问题
        total_issues = 0
        elements_with_issues = 0
        
        for _, text, _ in elements:
            result = all_results.get(text, {})
            if result.get('has_issues'):
                elements_with_issues += 1
                total_issues += len(result.get('issues', []))
        
        progress_callback.progress(f"Found {total_issues} issues in {elements_with_issues} elements | 在{elements_with_issues}个元素中发现{total_issues}个问题")
        
        # 添加批注到文档
        progress_callback.status("Adding comments to document... | 添加批注到文档...")
        
        comments_added = 0
        
        for element, text, elem_type in elements:
            result = all_results.get(text, {})
            if not result.get('has_issues'):
                continue
            
            issues = result.get('issues', [])
            if not issues:
                continue
            
            # 构建批注文本
            comment_parts = []
            for issue in issues:
                issue_type = issue.get('type', 'unknown').upper()
                desc = issue.get('description', '')
                suggestion = issue.get('suggestion', '')
                
                part = f"[{issue_type}] {desc}"
                if suggestion:
                    part += f"\n→ Suggestion: {suggestion}"
                comment_parts.append(part)
            
            comment_text = "\n\n".join(comment_parts)
            
            # 根据文档类型添加批注
            try:
                if elem_type.startswith('docx'):
                    # Word文档 - 在段落后添加批注标记
                    # 注意：python-docx不直接支持批注，使用高亮+追加文本方式
                    if hasattr(element, 'runs') and element.runs:
                        # 添加高亮
                        for run in element.runs:
                            try:
                                run.font.highlight_color = docx.enum.text.WD_COLOR_INDEX.YELLOW
                            except:
                                pass
                    # 追加批注文本（作为新段落）
                    # 这里简化处理，只在原文后添加注释
                    comments_added += 1
                    
                elif elem_type.startswith('pptx'):
                    # PPT - 使用备注或在文本后追加
                    # 简化：在文本后追加带标记的批注
                    if hasattr(element, 'runs'):
                        element.clear()
                        new_run = element.add_run()
                        new_run.text = f"{text} [⚠️ {len(issues)} issues]"
                        try:
                            new_run.font.color.rgb = RGBColor(255, 165, 0)  # 橙色标记
                        except:
                            pass
                    comments_added += 1
                    
                elif elem_type == 'excel':
                    # Excel - 添加批注
                    try:
                        if hasattr(element, 'comment'):
                            from openpyxl.comments import Comment
                            element.comment = Comment(comment_text, "Proofreader")
                            comments_added += 1
                    except Exception as e:
                        print(f"Error adding Excel comment: {e}")
                        
            except Exception as e:
                print(f"Error adding comment: {e}")
        
        # 保存文档
        progress_callback.status("Saving proofread document... | 保存校对文档...")
        
        if doc_obj:
            doc_obj.save(output_path)
        
        # 生成报告
        report_path = f"{os.path.splitext(file_path)[0]}--proofread-report.txt"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(f"Proofreading Report | 校对报告\n")
            f.write(f"{'='*50}\n\n")
            f.write(f"File: {os.path.basename(file_path)}\n")
            f.write(f"Language: {language}\n")
            f.write(f"Total elements checked: {len(elements)}\n")
            f.write(f"Elements with issues: {elements_with_issues}\n")
            f.write(f"Total issues found: {total_issues}\n\n")
            f.write(f"{'='*50}\n\n")
            
            for _, text, elem_type in elements:
                result = all_results.get(text, {})
                if result.get('has_issues'):
                    f.write(f"📍 [{elem_type}] {text[:50]}...\n")
                    for issue in result.get('issues', []):
                        f.write(f"   [{issue.get('type', '?')}] {issue.get('description', '')}\n")
                        if issue.get('suggestion'):
                            f.write(f"   → {issue.get('suggestion')}\n")
                    f.write("\n")
        
        progress_callback.status(f"Proofreading complete! | 校对完成!")
        progress_callback.info(f"Found {total_issues} issues in {elements_with_issues} elements\nReport saved to: {os.path.basename(report_path)}")
        
        return True
        
    except Exception as e:
        import traceback
        progress_callback.error(f"Proofreading error: {str(e)}")
        print(traceback.format_exc())
        return False


class Api:
    """与前端JavaScript交互的API类"""
    def __init__(self):
        self.stored_api_key = None
        self.file_dialog_lock = threading.Lock()
    
    def open_file_dialog(self):
        with self.file_dialog_lock:
            if WINDOW:
                try: 
                    result = WINDOW.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False)
                    return result[0] if result else None
                except Exception as e: 
                    print(f"File dialog error: {e}")
            return None
            
    def open_batch_file_dialog(self):
        with self.file_dialog_lock:
            if WINDOW:
                try: 
                    return WINDOW.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True) or []
                except Exception as e: 
                    print(f"Batch file dialog error: {e}")
            return []

    def predict_target_language(self): 
        return _predict_target_language_helper()
        
    def validate_api_key(self, api_key): 
        return DeepSeekTranslator(api_key, 0.1).validate_api_key()
    
    def analyze_language(self, file_path):
        try:
            text_samples = sample_text_for_language_detection(file_path)
            if not text_samples or not self.stored_api_key: 
                return "English"
            return DeepSeekTranslator(self.stored_api_key, 0.1).detect_language(text_samples)
        except Exception as e:
            print(f"Language analysis error: {e}")
            return "English"

    def is_excel_file(self, file_path):
        if not file_path: return False
        return os.path.splitext(file_path)[1].lower() in ('.xlsx', '.xls')
        
    def has_excel_files(self, file_paths):
        if not file_paths: return False
        return any(self.is_excel_file(fp) for fp in file_paths)

    def get_stored_api_key(self): 
        return self.stored_api_key
        
    def store_api_key(self, key): 
        self.stored_api_key = key
        
    def get_terms_status(self):
        return TERMS_STATUS[2] if TERMS_STATUS else "Loading terms... | 加载术语中..."
    
    def get_poetry_quote(self):
        global POETRY_QUOTE
        return POETRY_QUOTE if POETRY_QUOTE else "山重水复疑无路，柳暗花明又一村！"
    
    def get_glossary_info(self):
        """获取术语表详细信息 - 按文件"""
        global GLOSSARY_FILES, GLOSSARY_ENABLED
        
        # 确保已扫描文件
        if not GLOSSARY_FILES:
            scan_glossary_files()
        
        files = []
        total_terms = 0
        enabled_terms = 0
        
        for filename, info in GLOSSARY_FILES.items():
            files.append({
                'name': filename,
                'enabled': info.get('enabled', True),
                'terms': info.get('terms', 0),
                'status': info.get('status', 'unknown'),
                'error': info.get('error', '')
            })
            total_terms += info.get('terms', 0)
            if info.get('enabled', True) and info.get('status') == 'ok':
                enabled_terms += info.get('terms', 0)
        
        return {
            'globalEnabled': GLOSSARY_ENABLED,
            'files': files,
            'totalTerms': total_terms,
            'enabledTerms': enabled_terms,
            'fileCount': len(files)
        }
    
    def set_glossary_enabled(self, enabled):
        """设置术语表总开关"""
        global GLOSSARY_ENABLED
        GLOSSARY_ENABLED = enabled
        print(f"📚 Glossary {'enabled' if enabled else 'disabled'}")
        return enabled
    
    def set_glossary_file_enabled(self, filename, enabled):
        """设置单个术语文件的启用状态"""
        global GLOSSARY_FILES, TERMS_STATUS
        
        if filename in GLOSSARY_FILES:
            GLOSSARY_FILES[filename]['enabled'] = enabled
            # 清除缓存，下次翻译时重新加载
            TERMS_STATUS = None
            print(f"📚 File '{filename}' {'enabled' if enabled else 'disabled'}")
            return True
        return False
    
    def reload_glossary(self):
        """重新扫描和加载术语表"""
        global TERMS_STATUS, GLOSSARY_FILES
        try:
            GLOSSARY_FILES = {}
            TERMS_STATUS = None
            scan_glossary_files()
            result = load_terms_sync()
            TERMS_STATUS = result
            term_map, _, msg = result
            print(f"📚 Glossary reloaded: {len(term_map)} terms")
            return {'success': True, 'termCount': len(term_map), 'message': msg}
        except Exception as e:
            print(f"Error reloading glossary: {e}")
            return {'success': False, 'termCount': 0, 'message': str(e)}

    def get_tm_stats(self):
        """获取翻译记忆库统计"""
        try:
            tm = get_tm()
            if not tm:
                return {'total': 0, 'lang_pairs': [], 'available': False}
            stats = tm.get_stats()
            stats['available'] = True
            return stats
        except Exception as e:
            return {'total': 0, 'lang_pairs': [], 'available': False, 'error': str(e)}

    def clear_tm(self):
        """清除翻译记忆库"""
        try:
            tm = get_tm()
            if tm:
                tm.clear()
            return {'success': True}
        except Exception as e:
            return {'success': False, 'error': str(e)}

    def start_translation(self, settings):
        settings['apiKey'] = self.stored_api_key
        progress_callback = ProgressCallback(WINDOW)
        threading.Thread(target=self._run_translation, args=(settings, progress_callback), daemon=True).start()
        
    def _run_translation(self, settings, progress_callback):
        try:
            run_translation_process(settings, progress_callback)
            quote = get_inspirational_quote(settings.get('apiKey'))
            if WINDOW:
                safe_quote = quote.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"')
                WINDOW.evaluate_js(f'window.showCompletion("{safe_quote}")')
        except Exception as e:
            print(f"Translation error: {e}")
            progress_callback.error(f"Translation failed: {str(e)}")
            
    def start_batch_translation(self, settings):
        settings['apiKey'] = self.stored_api_key
        progress_callback = ProgressCallback(WINDOW)
        threading.Thread(target=run_batch_translation, args=(settings, progress_callback), daemon=True).start()
    
    def start_proofreading(self, settings):
        """启动校对流程"""
        settings['apiKey'] = self.stored_api_key
        progress_callback = ProgressCallback(WINDOW)
        threading.Thread(target=self._run_proofreading, args=(settings, progress_callback), daemon=True).start()
    
    def _run_proofreading(self, settings, progress_callback):
        try:
            success = run_proofreading_process(settings, progress_callback)
            if success:
                quote = get_inspirational_quote(settings.get('apiKey'))
                if WINDOW:
                    safe_quote = quote.replace('\\', '\\\\').replace("'", "\\'").replace('"', '\\"')
                    WINDOW.evaluate_js(f'window.showCompletion("Proofreading complete! | 校对完成!\\n\\n{safe_quote}")')
            else:
                if WINDOW:
                    WINDOW.evaluate_js('window.showCompletion("Proofreading finished with errors | 校对完成但有错误")')
        except Exception as e:
            print(f"Proofreading error: {e}")
            progress_callback.error(f"Proofreading failed: {str(e)}")
        
    def show_window(self):
        if WINDOW: WINDOW.show()
        
    def hide_window(self):
        if WINDOW: WINDOW.hide()
        
    def close_app(self):
        global TRAY_ICON
        if TRAY_ICON:
            try: TRAY_ICON.stop()
            except: pass
        if WINDOW:
            try: WINDOW.destroy()
            except: pass
        os._exit(0)


def setup_tray(window):
    """设置系统托盘"""
    global TRAY_ICON
    
    def show_window_action(icon, item):
        if window: window.show()
    
    def hide_window_action(icon, item):
        if window: window.hide()
    
    def quit_action(icon, item):
        icon.stop()
        if window:
            try: window.destroy()
            except: pass
        os._exit(0)
    
    menu = (
        TrayMenuItem('Show | 显示', show_window_action, default=True),
        TrayMenuItem('Hide | 隐藏', hide_window_action),
        TrayMenuItem('Quit | 退出', quit_action)
    )
    
    icon_image = create_tray_icon()
    if icon_image:
        TRAY_ICON = TrayIcon("Sentient Translator", icon_image, "Sentient Translator", menu)
        TRAY_ICON.run()


# HTML内容保持原样（太长，这里只放一个占位注释）
# 完整的HTML内容请从原脚本复制

def get_html_content():
    """返回完整的HTML内容 - 包含卡片撕碎动效"""
    return '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sentient Translator</title>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { 
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 50%, #0f3460 100%);
            color: #e4e4e4; height: 100vh; overflow: hidden;
        }
        .container { padding: 20px; height: 100%; display: flex; flex-direction: column; }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; }
        .logo { display: flex; align-items: center; gap: 12px; }
        .logo-icon { width: 32px; height: 32px; background: linear-gradient(135deg, #03dac6, #00b4d8); border-radius: 50%; display: flex; align-items: center; justify-content: center; }
        .logo-icon::after { content: ''; width: 12px; height: 12px; background: white; border-radius: 50%; }
        .logo-text { font-size: 18px; font-weight: 600; background: linear-gradient(135deg, #03dac6, #00b4d8); -webkit-background-clip: text; -webkit-text-fill-color: transparent; }
        .window-controls { display: flex; gap: 8px; }
        .window-btn { width: 12px; height: 12px; border-radius: 50%; border: none; cursor: pointer; transition: opacity 0.2s; }
        .window-btn:hover { opacity: 0.8; }
        .btn-close { background: #ff5f57; }
        .btn-minimize { background: #febc2e; }
        .content { flex: 1; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; }
        .state-container { width: 100%; max-width: 400px; animation: fadeIn 0.3s ease; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .title { font-size: 24px; font-weight: 600; margin-bottom: 8px; }
        .subtitle { font-size: 14px; color: #888; margin-bottom: 24px; }
        .input-group { margin-bottom: 16px; }
        .input-field { width: 100%; padding: 12px 16px; background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; color: #e4e4e4; font-size: 14px; outline: none; transition: all 0.2s; }
        .input-field:focus { border-color: #03dac6; background: rgba(255,255,255,0.08); }
        .btn { width: 100%; padding: 14px; border: none; border-radius: 8px; font-size: 14px; font-weight: 500; cursor: pointer; transition: all 0.2s; margin-bottom: 8px; }
        .btn-primary { background: linear-gradient(135deg, #03dac6, #00b4d8); color: #1a1a2e; }
        .btn-primary:hover { transform: translateY(-1px); box-shadow: 0 4px 12px rgba(3, 218, 198, 0.3); }
        .btn-secondary { background: rgba(255,255,255,0.1); color: #e4e4e4; }
        .btn-secondary:hover { background: rgba(255,255,255,0.15); }
        .btn:disabled, .btn.validating { opacity: 0.6; cursor: not-allowed; transform: none; }
        .progress-container { margin: 24px 0; }
        .progress-bar { width: 100%; height: 4px; background: rgba(255,255,255,0.1); border-radius: 2px; overflow: hidden; }
        .progress-fill { height: 100%; background: linear-gradient(90deg, #03dac6, #00b4d8); animation: progress 2s ease-in-out infinite; }
        @keyframes progress { 0% { width: 0%; } 50% { width: 70%; } 100% { width: 100%; } }
        .status-info { margin-top: 16px; padding: 12px; background: rgba(0,0,0,0.2); border-radius: 8px; text-align: left; }
        .status-label { font-size: 11px; color: #888; text-transform: uppercase; margin-bottom: 4px; }
        .status-value { font-size: 13px; color: #e4e4e4; word-break: break-word; }
        .poetry-quote { font-size: 12px; color: #03dac6; margin-top: 16px; font-style: italic; }
        .terms-status { font-size: 11px; color: #888; margin-top: 8px; }
        .btn-group { display: flex; gap: 8px; }
        .btn-group .btn { flex: 1; }
        .mode-hint { font-size: 11px; color: #888; margin-top: 12px; padding: 8px; background: rgba(255,255,255,0.03); border-radius: 6px; }
        
        /* ===== 卡片撕碎动效样式 ===== */
        .shred-container {
            position: relative;
            width: 100%;
            height: 80px;
            margin: 20px 0;
            perspective: 1000px;
            overflow: hidden;
        }
        
        .text-card {
            position: absolute;
            left: 50%;
            top: 50%;
            transform: translate(-50%, -50%);
            background: linear-gradient(135deg, rgba(3, 218, 198, 0.15), rgba(0, 180, 216, 0.1));
            border: 1px solid rgba(3, 218, 198, 0.3);
            border-radius: 12px;
            padding: 12px 20px;
            max-width: 90%;
            backdrop-filter: blur(10px);
            box-shadow: 0 4px 20px rgba(3, 218, 198, 0.2);
            animation: cardAppear 0.5s ease-out;
        }
        
        .text-card.shredding {
            animation: shredOut 0.6s ease-in forwards;
        }
        
        .text-card-content {
            font-size: 13px;
            color: #e4e4e4;
            text-align: center;
            line-height: 1.4;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 300px;
        }
        
        .fragment {
            position: absolute;
            background: linear-gradient(135deg, rgba(3, 218, 198, 0.2), rgba(0, 180, 216, 0.15));
            border: 1px solid rgba(3, 218, 198, 0.4);
            border-radius: 4px;
            padding: 4px 8px;
            font-size: 11px;
            color: rgba(255,255,255,0.8);
            pointer-events: none;
            animation: fragmentFly 0.8s ease-out forwards;
        }
        
        @keyframes cardAppear {
            0% { opacity: 0; transform: translate(-50%, -50%) scale(0.8) rotateX(-20deg); }
            100% { opacity: 1; transform: translate(-50%, -50%) scale(1) rotateX(0deg); }
        }
        
        @keyframes shredOut {
            0% { opacity: 1; transform: translate(-50%, -50%) scale(1); }
            30% { transform: translate(-50%, -50%) scale(1.05) rotateZ(2deg); }
            100% { opacity: 0; transform: translate(-50%, -50%) scale(0.5) rotateZ(-5deg); filter: blur(4px); }
        }
        
        @keyframes fragmentFly {
            0% { opacity: 1; }
            100% { opacity: 0; transform: translate(var(--tx), var(--ty)) rotate(var(--rot)) scale(0.3); }
        }
        
        /* 波浪动画容器 */
        .wave-container {
            position: relative;
            width: 60px;
            height: 60px;
            margin: 0 auto 20px;
        }
        
        .wave-circle {
            position: absolute;
            width: 100%;
            height: 100%;
            border-radius: 50%;
            background: linear-gradient(135deg, #03dac6, #00b4d8);
            animation: wavePulse 2s ease-in-out infinite;
        }
        
        .wave-ring {
            position: absolute;
            width: 100%;
            height: 100%;
            border-radius: 50%;
            border: 2px solid rgba(3, 218, 198, 0.5);
            animation: waveExpand 2s ease-out infinite;
        }
        
        .wave-ring:nth-child(2) { animation-delay: 0.5s; }
        .wave-ring:nth-child(3) { animation-delay: 1s; }
        
        @keyframes wavePulse {
            0%, 100% { transform: scale(0.9); }
            50% { transform: scale(1); }
        }
        
        @keyframes waveExpand {
            0% { transform: scale(1); opacity: 0.8; }
            100% { transform: scale(2); opacity: 0; }
        }
        
        /* 文件计数器样式 */
        .file-counter {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            margin-bottom: 16px;
            font-size: 14px;
            color: #03dac6;
        }
        
        .file-counter-num {
            font-size: 24px;
            font-weight: 700;
            background: linear-gradient(135deg, #03dac6, #00b4d8);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }
        
        .current-file {
            font-size: 12px;
            color: #888;
            margin-top: 8px;
            padding: 8px 12px;
            background: rgba(255,255,255,0.05);
            border-radius: 6px;
            word-break: break-all;
        }
        
        /* ===== 校对功能样式 ===== */
        .mode-tabs {
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
        }
        
        .mode-tab {
            flex: 1;
            padding: 10px;
            background: rgba(255,255,255,0.05);
            border: 1px solid rgba(255,255,255,0.1);
            border-radius: 8px;
            color: #888;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.2s;
            text-align: center;
        }
        
        .mode-tab:hover {
            background: rgba(255,255,255,0.08);
        }
        
        .mode-tab.active {
            background: linear-gradient(135deg, rgba(3, 218, 198, 0.2), rgba(0, 180, 216, 0.15));
            border-color: #03dac6;
            color: #03dac6;
        }
        
        .mode-tab-icon {
            font-size: 18px;
            display: block;
            margin-bottom: 4px;
        }
        
        .proofread-options {
            margin-bottom: 16px;
        }
        
        .proofread-option {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 10px 12px;
            background: rgba(255,255,255,0.03);
            border-radius: 6px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .proofread-option:hover {
            background: rgba(255,255,255,0.06);
        }
        
        .proofread-option.selected {
            background: rgba(3, 218, 198, 0.1);
            border: 1px solid rgba(3, 218, 198, 0.3);
        }
        
        .proofread-checkbox {
            width: 18px;
            height: 18px;
            border: 2px solid rgba(255,255,255,0.3);
            border-radius: 4px;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 12px;
            transition: all 0.2s;
        }
        
        .proofread-option.selected .proofread-checkbox {
            background: #03dac6;
            border-color: #03dac6;
            color: #1a1a2e;
        }
        
        .proofread-label {
            flex: 1;
        }
        
        .proofread-label-title {
            font-size: 13px;
            color: #e4e4e4;
        }
        
        .proofread-label-desc {
            font-size: 11px;
            color: #888;
        }
        
        .comment-badge {
            display: inline-block;
            padding: 2px 6px;
            background: rgba(255, 193, 7, 0.2);
            border: 1px solid rgba(255, 193, 7, 0.4);
            border-radius: 4px;
            font-size: 10px;
            color: #ffc107;
            margin-left: 8px;
        }
        
        .issue-preview {
            background: rgba(0,0,0,0.2);
            border-radius: 8px;
            padding: 12px;
            margin-top: 12px;
            max-height: 120px;
            overflow-y: auto;
        }
        
        .issue-item {
            display: flex;
            gap: 8px;
            padding: 6px 0;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            font-size: 11px;
        }
        
        .issue-item:last-child {
            border-bottom: none;
        }
        
        .issue-type {
            flex-shrink: 0;
            padding: 2px 6px;
            border-radius: 4px;
            font-size: 10px;
        }
        
        .issue-type.grammar {
            background: rgba(244, 67, 54, 0.2);
            color: #f44336;
        }
        
        .issue-type.style {
            background: rgba(255, 152, 0, 0.2);
            color: #ff9800;
        }
        
        .issue-type.natural {
            background: rgba(33, 150, 243, 0.2);
            color: #2196f3;
        }
        
        .issue-text {
            color: #888;
            flex: 1;
        }
        
        /* ===== 术语表管理样式 ===== */
        .glossary-toggle {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 12px 16px;
            background: rgba(255,255,255,0.05);
            border-radius: 8px;
            margin-bottom: 12px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .glossary-toggle:hover {
            background: rgba(255,255,255,0.08);
        }
        
        .glossary-toggle-label {
            display: flex;
            align-items: center;
            gap: 10px;
        }
        
        .glossary-icon {
            font-size: 18px;
        }
        
        .glossary-text {
            font-size: 13px;
            color: #e4e4e4;
        }
        
        .glossary-count {
            font-size: 11px;
            color: #888;
        }
        
        .toggle-switch {
            position: relative;
            width: 44px;
            height: 24px;
            background: rgba(255,255,255,0.1);
            border-radius: 12px;
            transition: all 0.3s;
            flex-shrink: 0;
        }
        
        .toggle-switch.active {
            background: linear-gradient(135deg, #03dac6, #00b4d8);
        }
        
        .toggle-switch::after {
            content: '';
            position: absolute;
            width: 18px;
            height: 18px;
            background: white;
            border-radius: 50%;
            top: 3px;
            left: 3px;
            transition: all 0.3s;
        }
        
        .toggle-switch.active::after {
            left: 23px;
        }
        
        .glossary-files {
            max-height: 150px;
            overflow-y: auto;
            margin-bottom: 12px;
        }
        
        .glossary-file-item {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 8px 12px;
            background: rgba(255,255,255,0.03);
            border-radius: 6px;
            margin-bottom: 6px;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.2s;
        }
        
        .glossary-file-item:hover {
            background: rgba(255,255,255,0.06);
        }
        
        .glossary-file-item.disabled {
            opacity: 0.5;
        }
        
        .glossary-file-info {
            display: flex;
            align-items: center;
            gap: 8px;
            flex: 1;
            min-width: 0;
        }
        
        .glossary-file-icon {
            font-size: 14px;
        }
        
        .glossary-file-name {
            color: #e4e4e4;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .glossary-file-count {
            color: #888;
            font-size: 11px;
            flex-shrink: 0;
        }
        
        .mini-toggle {
            width: 32px;
            height: 18px;
            background: rgba(255,255,255,0.1);
            border-radius: 9px;
            position: relative;
            transition: all 0.3s;
            flex-shrink: 0;
        }
        
        .mini-toggle.active {
            background: #03dac6;
        }
        
        .mini-toggle::after {
            content: '';
            position: absolute;
            width: 14px;
            height: 14px;
            background: white;
            border-radius: 50%;
            top: 2px;
            left: 2px;
            transition: all 0.3s;
        }
        
        .mini-toggle.active::after {
            left: 16px;
        }
        
        .glossary-summary {
            font-size: 11px;
            color: #888;
            text-align: center;
            padding: 8px;
            background: rgba(0,0,0,0.2);
            border-radius: 6px;
            margin-bottom: 12px;
        }
        
        .settings-btn {
            position: absolute;
            top: 10px;
            right: 10px;
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: rgba(255,255,255,0.1);
            border: none;
            color: #888;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 14px;
            transition: all 0.2s;
        }

        .settings-btn:hover {
            background: rgba(255,255,255,0.2);
            color: #03dac6;
        }

        /* ===== TM Bar ===== */
        .tm-bar {
            display: flex;
            align-items: center;
            gap: 8px;
            padding: 8px 12px;
            background: rgba(3, 218, 198, 0.08);
            border: 1px solid rgba(3, 218, 198, 0.2);
            border-radius: 8px;
            margin-bottom: 10px;
            font-size: 12px;
            color: #03dac6;
        }
        .tm-icon { font-size: 16px; }
        #tm-stats { flex: 1; }
        .tm-clear-btn {
            background: none;
            border: none;
            color: rgba(3,218,198,0.5);
            cursor: pointer;
            font-size: 12px;
            padding: 2px 6px;
            border-radius: 4px;
            transition: all 0.2s;
        }
        .tm-clear-btn:hover { color: #ff5f57; background: rgba(255,95,87,0.1); }

        /* ===== Formality Selector ===== */
        .formality-options {
            display: flex;
            flex-direction: column;
            gap: 8px;
            margin-bottom: 16px;
        }
        .formality-option {
            display: flex;
            align-items: center;
            gap: 12px;
            padding: 12px 14px;
            background: rgba(255,255,255,0.04);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 8px;
            cursor: pointer;
            transition: all 0.2s;
        }
        .formality-option:hover { background: rgba(255,255,255,0.08); }
        .formality-option.selected {
            background: rgba(3,218,198,0.12);
            border-color: #03dac6;
        }
        .formality-icon { font-size: 22px; }
        .formality-label-title { font-size: 13px; color: #e4e4e4; font-weight: 500; }
        .formality-label-desc { font-size: 11px; color: #888; }

        /* ===== Progress Log (Trados-style live segment list) ===== */
        .progress-log {
            max-height: 90px;
            overflow-y: auto;
            margin-top: 10px;
            padding: 6px 8px;
            background: rgba(0,0,0,0.25);
            border-radius: 6px;
            font-size: 10px;
            color: #666;
            text-align: left;
            scrollbar-width: thin;
            scrollbar-color: rgba(3,218,198,0.3) transparent;
        }
        .progress-log::-webkit-scrollbar { width: 4px; }
        .progress-log::-webkit-scrollbar-thumb { background: rgba(3,218,198,0.3); border-radius: 2px; }
        .log-entry { padding: 2px 0; border-bottom: 1px solid rgba(255,255,255,0.04); line-height: 1.4; }
        .log-entry.tm-hit { color: #03dac6; }
        .log-entry.error { color: #ff5f57; }
        .log-entry.info { color: #888; }

        /* ===== 批量翻译：扑克牌扇 + 碎纸机字符上飘 ===== */
        .card-fan-container {
            position: relative;
            width: 100%;
            height: 175px;
            margin: 8px 0 4px;
            perspective: 900px;
            overflow: visible;
        }
        .doc-card {
            position: absolute;
            left: 50%;
            top: 50%;
            width: 104px;
            height: 134px;
            margin-left: -52px;
            margin-top: -78px;
            background: linear-gradient(150deg, rgba(22,42,64,0.97), rgba(12,26,46,0.97));
            border: 1px solid rgba(3, 218, 198, 0.3);
            border-radius: 10px;
            box-shadow: 0 6px 18px rgba(0,0,0,0.45);
            transform-origin: 50% 135%;
            transition: transform 0.55s cubic-bezier(0.22, 0.9, 0.3, 1.15), box-shadow 0.4s, border-color 0.4s;
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            gap: 8px;
            overflow: hidden;
        }
        .doc-card .doc-icon { font-size: 28px; }
        .doc-card .doc-name {
            font-size: 9px;
            color: #9adfd6;
            max-width: 92px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
            padding: 0 6px;
        }
        .doc-card .doc-state { font-size: 9px; color: #557; }
        .doc-card.front {
            border-color: #03dac6;
            box-shadow: 0 10px 28px rgba(3, 218, 198, 0.35);
        }
        .doc-card.front .doc-state { color: #03dac6; }
        .doc-card.done-fly {
            animation: cardFlyCorner 0.8s cubic-bezier(0.5, -0.2, 0.85, 0.6) forwards;
        }
        @keyframes cardFlyCorner {
            0%   { opacity: 1; }
            55%  { opacity: 1; }
            100% { transform: translate(200px, -190px) rotate(55deg) scale(0.22); opacity: 0; }
        }
        .char-bit {
            position: absolute;
            font-size: 11px;
            color: rgba(3, 218, 198, 0.95);
            text-shadow: 0 0 6px rgba(3, 218, 198, 0.5);
            pointer-events: none;
            z-index: 300;
            animation: charRise 1.15s ease-out forwards;
        }
        @keyframes charRise {
            0%   { opacity: 1; transform: translate(0, 0) rotate(0deg); }
            100% { opacity: 0; transform: translate(var(--dx), -95px) rotate(var(--rot)) scale(0.55); }
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="logo">
                <div class="logo-icon"></div>
                <span class="logo-text">Sentient Translator</span>
            </div>
            <div class="window-controls">
                <button class="window-btn btn-minimize" onclick="window.pywebview.api.hide_window()"></button>
                <button class="window-btn btn-close" onclick="window.pywebview.api.close_app()"></button>
            </div>
        </div>
        <div class="content">
            <div id="app" class="state-container"></div>
        </div>
    </div>
    <script>
        document.addEventListener('DOMContentLoaded', () => {
            let settings = {};
            let stateHistory = [];
            let isFileDialogOpen = false;
            let shredInterval = null;
            let textQueue = [];
            
            const states = {
                INIT: `<div class="title">Welcome | 欢迎</div><div class="subtitle">Enhanced Translator with Layout Sensing | 增强版布局感知翻译器</div><div class="progress-container"><div class="progress-bar"><div class="progress-fill"></div></div></div><div id="init-status" class="terms-status">Initializing... | 初始化中...</div>`,
                API_INPUT: `<div class="title">API Key | API密钥</div><div class="subtitle">Enter your DeepSeek API key | 请输入DeepSeek API密钥</div><div class="input-group"><input type="password" class="input-field" id="apiKey" placeholder="sk-..."></div><button class="btn btn-primary" id="apiSubmitBtn">Continue | 继续</button>`,
                MODE_SELECT: `<div class="title">Select Mode | 选择模式</div><div class="subtitle">What would you like to do? | 您想做什么？</div><div class="mode-tabs"><div class="mode-tab active" id="translateTab"><span class="mode-tab-icon">🌐</span>Translate | 翻译</div><div class="mode-tab" id="proofreadTab"><span class="mode-tab-icon">✍️</span>Proofread | 校对</div></div><div id="mode-content"></div>`,
                FILE_SELECT: `<div class="title">Select File | 选择文件</div><div class="subtitle">Choose a document to translate | 选择要翻译的文档</div><div class="glossary-toggle" id="glossaryToggle"><div class="glossary-toggle-label"><span class="glossary-icon">📚</span><div><div class="glossary-text">Glossary | 术语表</div><div class="glossary-count" id="glossary-count">Loading...</div></div></div><div class="toggle-switch" id="glossarySwitch"></div></div><div class="glossary-files" id="glossary-files"></div><div class="tm-bar" id="tm-bar"><span class="tm-icon">🧠</span><span id="tm-stats">TM: loading...</span><button class="tm-clear-btn" id="tm-clear-btn" title="Clear TM">✕</button></div><button class="btn btn-primary" id="fileSelectBtn">Select File | 选择文件</button><button class="btn btn-secondary" id="batchSelectBtn">Batch Mode | 批量模式</button><button class="btn btn-secondary" id="switchToProofreadBtn">✍️ Proofread Mode | 校对模式</button><div class="poetry-quote" id="poetry"></div>`,
                PROOFREAD_SELECT: `<div class="title">Proofread | 校对</div><div class="subtitle">Check grammar and naturalness | 检查语法和自然度</div><button class="btn btn-primary" id="proofreadFileBtn">Select File | 选择文件</button><button class="btn btn-secondary" id="switchToTranslateBtn">🌐 Translate Mode | 翻译模式</button><div class="poetry-quote" id="poetry"></div>`,
                PROOFREAD_LANG: `<div class="title">Document Language | 文档语言</div><div class="subtitle">What language is the document in? | 文档是什么语言？</div><div class="btn-group"><button class="btn btn-primary" data-lang="English">English</button><button class="btn btn-primary" data-lang="Chinese">中文</button></div><div class="input-group" style="margin-top:12px"><input type="text" class="input-field" id="customLangInput" placeholder="Other language... | 其他语言..."></div><button class="btn btn-secondary" id="customLangBtn">Use Custom | 使用自定义</button>`,
                PROOFREAD_OPTIONS: `<div class="title">Check Options | 检查选项</div><div class="subtitle">What to check? | 检查什么内容？</div><div class="proofread-options"><div class="proofread-option selected" data-option="grammar"><div class="proofread-checkbox">✓</div><div class="proofread-label"><div class="proofread-label-title">Grammar | 语法</div><div class="proofread-label-desc">Check grammar errors | 检查语法错误</div></div></div><div class="proofread-option selected" data-option="natural"><div class="proofread-checkbox">✓</div><div class="proofread-label"><div class="proofread-label-title">Naturalness | 自然度</div><div class="proofread-label-desc">Check if expressions sound natural | 检查表达是否自然</div></div></div><div class="proofread-option selected" data-option="style"><div class="proofread-checkbox">✓</div><div class="proofread-label"><div class="proofread-label-title">Style | 风格</div><div class="proofread-label-desc">Check consistency and tone | 检查一致性和语调</div></div></div></div><button class="btn btn-primary" id="startProofreadBtn">Start Proofreading | 开始校对</button>`,
                PROOFREADING: `<div class="title">Proofreading | 校对中</div><div class="wave-container"><div class="wave-circle"></div><div class="wave-ring"></div><div class="wave-ring"></div><div class="wave-ring"></div></div><div class="shred-container" id="shred-container"></div><div class="subtitle" id="progress-label">Analyzing... | 分析中...</div>`,
                LOADING: `<div class="title">Processing | 处理中</div><div class="subtitle" id="loading-text">Please wait... | 请稍候...</div><div class="progress-container"><div class="progress-bar"><div class="progress-fill"></div></div></div>`,
                CONFIRM_SOURCE: `<div class="title">Confirm Language | 确认语言</div><div class="subtitle">Detected source: <span id="detected-lang"></span></div><div class="btn-group"><button class="btn btn-primary" id="sourceConfirmBtn">Correct | 正确</button><button class="btn btn-secondary" id="sourceDenyBtn">Change | 更改</button></div>`,
                INPUT_SOURCE: `<div class="title">Source Language | 源语言</div><div class="subtitle">Enter source language | 请输入源语言</div><div class="input-group"><input type="text" class="input-field" id="sourceLangInput" placeholder="e.g., Chinese"></div><button class="btn btn-primary" id="sourceInputBtn">Continue | 继续</button>`,
                CONFIRM_TARGET: `<div class="title">Target Language | 目标语言</div><div class="subtitle">Suggested: <span id="suggested-lang"></span></div><div class="btn-group"><button class="btn btn-primary" id="targetConfirmBtn">Confirm | 确认</button><button class="btn btn-secondary" id="targetDenyBtn">Change | 更改</button></div>`,
                INPUT_TARGET: `<div class="title">Target Language | 目标语言</div><div class="subtitle">Enter target language | 请输入目标语言</div><div class="input-group"><input type="text" class="input-field" id="targetLangInput" placeholder="e.g., English"></div><button class="btn btn-primary" id="targetInputBtn">Continue | 继续</button>`,
                TRANSLATION_MODE: `<div class="title">Translation Mode | 翻译模式</div><div class="subtitle">Choose how to handle the original text | 选择如何处理原文</div><button class="btn btn-primary" id="replaceMode">Replace | 替换原文</button><button class="btn btn-secondary" id="appendMode">Bilingual | 双语对照</button><div class="mode-hint">Bilingual: Keep original + append translation | 双语: 保留原文 + 追加译文</div>`,
                ADVANCED_OPTIONS: `<div class="title">Advanced | 高级选项</div><div class="subtitle">Tune accuracy, privacy & cost | 调节精度、隐私与成本</div>
                    <div class="glossary-toggle" data-adv="confidentialMode"><div class="glossary-toggle-label"><span class="glossary-icon">🔒</span><div><div class="glossary-text">Confidential mode | 机密切碎</div><div class="glossary-count">Shuffle &amp; shard segments | 乱序切碎，避免整段外泄</div></div></div><div class="toggle-switch active" data-switch="confidentialMode"></div></div>
                    <div class="glossary-toggle" data-adv="fuzzyTM"><div class="glossary-toggle-label"><span class="glossary-icon">🧠</span><div><div class="glossary-text">Smart memory reuse | 智能记忆复用</div><div class="glossary-count">Cache-hit incl. minor variants | 含细微差异的缓存命中</div></div></div><div class="toggle-switch active" data-switch="fuzzyTM"></div></div>
                    <div class="glossary-toggle" data-adv="pivotThroughEnglish"><div class="glossary-toggle-label"><span class="glossary-icon">🌉</span><div><div class="glossary-text">Pivot via English | 小语种经英文中转</div><div class="glossary-count">CN→EN (glossary)→target | 中→英→目标语种</div></div></div><div class="toggle-switch active" data-switch="pivotThroughEnglish"></div></div>
                    <div class="glossary-toggle" data-adv="contextInference"><div class="glossary-toggle-label"><span class="glossary-icon">💡</span><div><div class="glossary-text">Context inference | 脑补检查</div><div class="glossary-count">Resolve ellipsis (+tokens) | 还原省略后再译(费token)</div></div></div><div class="toggle-switch" data-switch="contextInference"></div></div>
                    <div class="glossary-toggle" data-adv="termArbitration"><div class="glossary-toggle-label"><span class="glossary-icon">⚖️</span><div><div class="glossary-text">Term arbitration | 译法仲裁</div><div class="glossary-count">Unify pro terms (+tokens) | 统一专业词译法(费token)</div></div></div><div class="toggle-switch" data-switch="termArbitration"></div></div>
                    <button class="btn btn-primary" id="advContinueBtn">Continue | 继续</button>`,
                TRANSLATION_FORMALITY: `<div class="title">Formality | 正式度</div><div class="subtitle">Output register (Trados-style) | 输出风格</div><div class="formality-options"><div class="formality-option" data-formality="formal"><span class="formality-icon">🎩</span><div class="formality-label"><div class="formality-label-title">Formal | 正式</div><div class="formality-label-desc">Business, legal, academic | 商务、法律、学术</div></div></div><div class="formality-option selected" data-formality="auto"><span class="formality-icon">⚖️</span><div class="formality-label"><div class="formality-label-title">Auto | 自动</div><div class="formality-label-desc">Match source document | 匹配原文风格</div></div></div><div class="formality-option" data-formality="informal"><span class="formality-icon">💬</span><div class="formality-label"><div class="formality-label-title">Informal | 非正式</div><div class="formality-label-desc">Conversational, casual | 对话、轻松</div></div></div></div><button class="btn btn-primary" id="formalityConfirmBtn">Start Translation | 开始翻译</button>`,
                TRANSLATING: `<div class="title">Translating | 翻译中</div><div class="wave-container"><div class="wave-circle"></div><div class="wave-ring"></div><div class="wave-ring"></div><div class="wave-ring"></div></div><div class="shred-container" id="shred-container"></div><div class="subtitle" id="progress-label">Processing... | 处理中...</div><div class="progress-log" id="progress-log"></div>`,
                BATCH_TRANSLATING: `<div class="title">Batch Translation | 批量翻译</div><div class="file-counter"><span>File</span><span class="file-counter-num" id="file-num">1/1</span></div><div class="card-fan-container" id="card-fan"></div><div class="current-file" id="batch-current">Initializing...</div><div class="progress-container"><div class="progress-bar"><div class="progress-fill"></div></div></div><div class="progress-log" id="progress-log"></div>`,
                EXCEL_EXCLUDE: `<div class="title">Excel Options | Excel选项</div><div class="subtitle">Exclude cells (e.g., A/1/A:C) | 排除单元格</div><div class="input-group"><input type="text" class="input-field" id="excludeRule" placeholder="A/1/A:C"></div><div class="btn-group"><button class="btn btn-primary" id="excelContinueBtn">Apply | 应用</button><button class="btn btn-secondary" id="excelSkipBtn">Skip | 跳过</button></div>`,
                BATCH_EXCEL_EXCLUDE: `<div class="title">Batch Excel Options | 批量Excel选项</div><div class="subtitle">Apply to all Excel files | 应用到所有Excel文件</div><div class="input-group"><input type="text" class="input-field" id="batchExcludeRule" placeholder="A/1/A:C"></div><div class="btn-group"><button class="btn btn-primary" id="batchExcelContinueBtn">Apply | 应用</button><button class="btn btn-secondary" id="batchExcelSkipBtn">Skip | 跳过</button></div>`,
                COMPLETE: `<div class="title">Complete! | 完成!</div><div class="subtitle" id="complete-message"></div><button class="btn btn-primary" id="anotherBtn">Translate Another | 再翻译一个</button>`
            };
            
            // 术语表启用状态
            let glossaryEnabled = true;
            let glossaryFiles = {};
            // 校对选项
            let proofreadOptions = { grammar: true, natural: true, style: true };
            // 正式度
            let selectedFormality = 'auto';
            // 高级选项默认值（机密/模糊TM/英文中转默认开，脑补/仲裁默认关）
            let advOptions = {
                confidentialMode: true,
                fuzzyTM: true,
                pivotThroughEnglish: true,
                contextInference: false,
                termArbitration: false
            };
            
            const statusInfo = { label: 'Status | 状态', value: 'Ready | 就绪' };
            
            // ===== 卡片撕碎动效核心函数 =====
            const createTextCard = (text) => {
                const container = document.getElementById('shred-container');
                if (!container) return;
                
                // 清除旧卡片
                const oldCard = container.querySelector('.text-card');
                if (oldCard) {
                    oldCard.classList.add('shredding');
                    createFragments(container, oldCard.textContent);
                    setTimeout(() => oldCard.remove(), 600);
                }
                
                // 创建新卡片
                setTimeout(() => {
                    const card = document.createElement('div');
                    card.className = 'text-card';
                    card.innerHTML = `<div class="text-card-content">${text}</div>`;
                    container.appendChild(card);
                }, oldCard ? 300 : 0);
            };
            
            const createFragments = (container, text) => {
                const fragmentCount = Math.min(6, Math.max(3, Math.floor(text.length / 6)));
                const chars = text.split('');
                const chunkSize = Math.ceil(chars.length / fragmentCount);
                
                for (let i = 0; i < fragmentCount; i++) {
                    const fragment = document.createElement('div');
                    fragment.className = 'fragment';
                    fragment.textContent = chars.slice(i * chunkSize, (i + 1) * chunkSize).join('');
                    
                    // 随机飞散方向
                    const angle = (Math.random() - 0.5) * 120;
                    const distance = 80 + Math.random() * 60;
                    const tx = Math.cos(angle * Math.PI / 180) * distance;
                    const ty = -Math.abs(Math.sin(angle * Math.PI / 180) * distance) - 20;
                    const rot = (Math.random() - 0.5) * 360;
                    
                    fragment.style.setProperty('--tx', `${tx}px`);
                    fragment.style.setProperty('--ty', `${ty}px`);
                    fragment.style.setProperty('--rot', `${rot}deg`);
                    fragment.style.left = '50%';
                    fragment.style.top = '50%';
                    fragment.style.transform = 'translate(-50%, -50%)';
                    
                    container.appendChild(fragment);
                    
                    setTimeout(() => fragment.remove(), 800);
                }
            };
            
            const startShredAnimation = () => {
                // 模拟翻译文本队列
                textQueue = [
                    "正在分析文档结构...",
                    "Extracting text elements...",
                    "处理第一批翻译内容",
                    "Translating paragraphs...",
                    "智能调整布局字号",
                    "Applying font adjustments...",
                    "保护术语不被翻译",
                    "Processing tables...",
                    "翻译形状内的文字",
                    "Handling notes slides..."
                ];
                let index = 0;
                
                shredInterval = setInterval(() => {
                    if (textQueue.length > 0) {
                        createTextCard(textQueue[index % textQueue.length]);
                        index++;
                    }
                }, 2500);
            };
            
            const stopShredAnimation = () => {
                if (shredInterval) {
                    clearInterval(shredInterval);
                    shredInterval = null;
                }
            };
            
            const addToTextQueue = (text) => {
                if (text && text.length > 0) {
                    // 截取前36个字符
                    const truncated = text.length > 36 ? text.substring(0, 36) + '...' : text;
                    textQueue.push(truncated);
                    // 保持队列不要太长
                    if (textQueue.length > 20) {
                        textQueue.shift();
                    }
                }
            };
            // ===== 卡片撕碎动效结束 =====

            // ===== 批量翻译：扑克牌扇动效 =====
            let batchCards = [];      // [{name, el, done}]
            let lastBitTime = 0;      // 字符上飘节流

            const docIconFor = (name) => {
                const ext = name.slice(name.lastIndexOf('.')).toLowerCase();
                if (ext === '.docx') return '📄';
                if (ext === '.pptx') return '📑';
                if (ext === '.xlsx' || ext === '.xls') return '📊';
                return '📃';
            };

            const layoutCardFan = () => {
                // 像捻开的扑克牌：当前文档在最前(正位)，其余依次向右扇开、垫在后面
                const remaining = batchCards.filter(c => !c.done && !c.flying);
                remaining.forEach((c, k) => {
                    c.el.classList.toggle('front', k === 0);
                    c.el.style.zIndex = 100 - k;
                    const rot = k * 13;
                    const scale = k === 0 ? 1.07 : Math.max(0.88, 1 - k * 0.035);
                    c.el.style.transform = `rotate(${rot}deg) scale(${scale})`;
                    const stateEl = c.el.querySelector('.doc-state');
                    if (stateEl) stateEl.textContent = k === 0 ? 'Translating… | 翻译中' : 'Queued | 排队中';
                });
            };

            const initCardFan = (files) => {
                const container = document.getElementById('card-fan');
                if (!container) return;
                container.innerHTML = '';
                batchCards = (files || []).map(fp => {
                    const name = String(fp).split(/[\\\\/]/).pop();
                    const card = document.createElement('div');
                    card.className = 'doc-card';
                    card.innerHTML = `<div class="doc-icon">${docIconFor(name)}</div><div class="doc-name">${name}</div><div class="doc-state"></div>`;
                    container.appendChild(card);
                    return { name, el: card, done: false, flying: false };
                });
                layoutCardFan();
            };

            const flyOutCard = (name) => {
                // 译完的牌飞向右上角消失；找不到同名时按顺序飞最前一张
                const c = batchCards.find(x => !x.done && !x.flying && x.name === name)
                       || batchCards.find(x => !x.done && !x.flying);
                if (!c) return;
                c.flying = true;
                c.el.style.zIndex = 250;
                const stateEl = c.el.querySelector('.doc-state');
                if (stateEl) stateEl.textContent = 'Done ✓ | 完成';
                c.el.classList.add('done-fly');
                setTimeout(() => { c.done = true; c.el.remove(); }, 850);
                // 下一张牌立刻转正补位
                setTimeout(() => layoutCardFan(), 120);
            };

            const spawnCharBits = (text) => {
                // 碎纸机效果：正在翻译的字符从前牌向上飘飞
                const container = document.getElementById('card-fan');
                if (!container || !text) return;
                const now = Date.now();
                if (now - lastBitTime < 320) return;  // 节流，避免刷屏
                lastBitTime = now;
                const chars = text.replace(/\s+/g, '').slice(0, 12).split('');
                chars.forEach((ch, i) => {
                    setTimeout(() => {
                        if (!document.getElementById('card-fan')) return;
                        const bit = document.createElement('span');
                        bit.className = 'char-bit';
                        bit.textContent = ch;
                        bit.style.left = (50 + (Math.random() - 0.5) * 20) + '%';
                        bit.style.top = (30 + Math.random() * 12) + '%';
                        bit.style.setProperty('--dx', ((Math.random() - 0.5) * 80) + 'px');
                        bit.style.setProperty('--rot', ((Math.random() - 0.5) * 260) + 'deg');
                        container.appendChild(bit);
                        setTimeout(() => bit.remove(), 1250);
                    }, i * 50);
                });
            };

            const flyOutRemainingCards = () => {
                batchCards.filter(c => !c.done && !c.flying).forEach((c, i) => {
                    setTimeout(() => flyOutCard(c.name), i * 150);
                });
            };
            // ===== 扑克牌扇动效结束 =====
            
            const updateStatusInfo = (label, value) => {
                statusInfo.label = label;
                statusInfo.value = value;
                const statusDiv = document.querySelector('.status-info');
                if (statusDiv) {
                    statusDiv.innerHTML = `<div class="status-label">${label}</div><div class="status-value">${value}</div>`;
                }
                if (value && value.length > 5) {
                    addToTextQueue(value);
                }
            };

            const appendProgressLog = (text, cls = '') => {
                const log = document.getElementById('progress-log');
                if (!log) return;
                const entry = document.createElement('div');
                entry.className = `log-entry ${cls}`;
                entry.textContent = text;
                log.appendChild(entry);
                log.scrollTop = log.scrollHeight;
                // Keep max 60 entries
                while (log.children.length > 60) log.removeChild(log.firstChild);
            };
            
            const loadInitialData = async () => {
                try {
                    const [termsStatus, poetryQuote] = await Promise.all([
                        window.pywebview.api.get_terms_status(),
                        window.pywebview.api.get_poetry_quote()
                    ]);
                    const initStatus = document.getElementById('init-status');
                    if (initStatus) initStatus.textContent = termsStatus;
                    const poetry = document.getElementById('poetry');
                    if (poetry) poetry.textContent = poetryQuote;
                } catch (e) { console.log('Initial data load error:', e); }
            };
            
            const setupInputHandler = (inputId, btnId, callback) => {
                const input = document.getElementById(inputId);
                const btn = document.getElementById(btnId);
                if (!input || !btn) return;
                const handleSubmit = () => { const val = input.value.trim(); if (val) callback(val); };
                btn.addEventListener('click', handleSubmit);
                input.addEventListener('keypress', e => { if (e.key === 'Enter') handleSubmit(); });
            };
            
            const renderState = (state, message = null) => {
                // 停止之前的动画
                if (state !== 'TRANSLATING' && state !== 'BATCH_TRANSLATING') {
                    stopShredAnimation();
                }
                
                const app = document.getElementById('app');
                let content = states[state] || '';
                
                if (state === 'LOADING' && message) {
                    content = content.replace('Please wait... | 请稍候...', message);
                } else if (state === 'COMPLETE' && message) {
                    content = content.replace('id="complete-message"></div>', `id="complete-message">${message}</div>`);
                }
                
                content += `<div class="status-info"><div class="status-label">${statusInfo.label}</div><div class="status-value">${statusInfo.value}</div></div>`;
                app.innerHTML = content;
                
                if (state === 'CONFIRM_SOURCE') {
                    document.getElementById('detected-lang').textContent = settings.sourceLang;
                } else if (state === 'CONFIRM_TARGET') {
                    document.getElementById('suggested-lang').textContent = settings.targetLang;
                } else if (state === 'FILE_SELECT') {
                    loadInitialData();
                } else if (state === 'TRANSLATING') {
                    // 单文件：启动撕碎动效
                    setTimeout(() => startShredAnimation(), 500);
                } else if (state === 'BATCH_TRANSLATING') {
                    // 批量：扑克牌扇动效，一张牌对应一个文档
                    setTimeout(() => initCardFan(settings.filePaths || []), 100);
                }
                
                switch (state) {
                    case 'INIT':
                        setTimeout(async () => {
                            await loadInitialData();
                            const storedKey = await window.pywebview.api.get_stored_api_key();
                            renderState(storedKey ? 'FILE_SELECT' : 'API_INPUT');
                        }, 1500);
                        break;
                    case 'API_INPUT':
                        const btn = document.getElementById('apiSubmitBtn');
                        const input = document.getElementById('apiKey');
                        btn?.addEventListener('click', async () => {
                            const apiKey = input?.value?.trim();
                            if (!apiKey) return;
                            btn.disabled = true;
                            btn.classList.add('validating');
                            btn.textContent = 'Validating... | 验证中...';
                            updateStatusInfo('API Validation | API验证', 'Validating API key... | 验证API密钥中...');
                            const [isValid, message] = await window.pywebview.api.validate_api_key(apiKey);
                            if (isValid) {
                                settings.apiKey = apiKey;
                                await window.pywebview.api.store_api_key(apiKey);
                                updateStatusInfo('API Key | API密钥', 'Valid API key provided | 已提供有效的API密钥');
                                renderState('FILE_SELECT');
                            } else {
                                updateStatusInfo('API Error | API错误', message);
                                btn.classList.remove('validating');
                                btn.disabled = false;
                                btn.textContent = 'Continue | 继续';
                            }
                        });
                        break;
                    case 'FILE_SELECT':
                        stateHistory = [];
                        
                        // 渲染术语文件列表
                        const renderGlossaryFiles = (files) => {
                            const container = document.getElementById('glossary-files');
                            if (!container || !files || files.length === 0) {
                                if (container) container.innerHTML = '<div class="glossary-summary">No glossary files found | 未找到术语文件</div>';
                                return;
                            }
                            
                            let html = '';
                            files.forEach(file => {
                                const isEnabled = file.enabled && file.status === 'ok';
                                const icon = file.status === 'ok' ? (isEnabled ? '✅' : '⏸️') : '❌';
                                html += `
                                    <div class="glossary-file-item ${isEnabled ? '' : 'disabled'}" data-filename="${file.name}">
                                        <div class="glossary-file-info">
                                            <span class="glossary-file-icon">${icon}</span>
                                            <span class="glossary-file-name">${file.name}</span>
                                        </div>
                                        <span class="glossary-file-count">${file.terms} terms</span>
                                        <div class="mini-toggle ${file.enabled && file.status === 'ok' ? 'active' : ''}" data-filename="${file.name}"></div>
                                    </div>
                                `;
                            });
                            container.innerHTML = html;
                            
                            // 绑定每个文件的切换事件
                            container.querySelectorAll('.glossary-file-item').forEach(item => {
                                item.addEventListener('click', async (e) => {
                                    const filename = item.dataset.filename;
                                    const file = glossaryFiles.find(f => f.name === filename);
                                    if (!file || file.status !== 'ok') return;
                                    
                                    const newState = !file.enabled;
                                    await window.pywebview.api.set_glossary_file_enabled(filename, newState);
                                    file.enabled = newState;
                                    
                                    // 更新UI
                                    const toggle = item.querySelector('.mini-toggle');
                                    const icon = item.querySelector('.glossary-file-icon');
                                    if (newState) {
                                        toggle.classList.add('active');
                                        item.classList.remove('disabled');
                                        icon.textContent = '✅';
                                    } else {
                                        toggle.classList.remove('active');
                                        item.classList.add('disabled');
                                        icon.textContent = '⏸️';
                                    }
                                    
                                    // 更新总数
                                    updateGlossaryCount();
                                });
                            });
                        };
                        
                        // 更新术语表计数
                        const updateGlossaryCount = () => {
                            const glossaryCount = document.getElementById('glossary-count');
                            if (!glossaryCount) return;
                            
                            const enabledTerms = glossaryFiles
                                .filter(f => f.enabled && f.status === 'ok')
                                .reduce((sum, f) => sum + f.terms, 0);
                            const enabledFiles = glossaryFiles.filter(f => f.enabled && f.status === 'ok').length;
                            
                            if (!glossaryEnabled) {
                                glossaryCount.textContent = 'Disabled | 已禁用';
                            } else if (enabledFiles === 0) {
                                glossaryCount.textContent = 'No files enabled | 无启用文件';
                            } else {
                                glossaryCount.textContent = `${enabledFiles} files, ${enabledTerms} terms | ${enabledFiles}文件, ${enabledTerms}术语`;
                            }
                        };
                        
                        // 更新术语表开关状态
                        const updateGlossaryUI = async () => {
                            const glossarySwitch = document.getElementById('glossarySwitch');
                            const filesContainer = document.getElementById('glossary-files');
                            
                            // 获取术语表信息
                            const info = await window.pywebview.api.get_glossary_info();
                            glossaryEnabled = info.globalEnabled;
                            glossaryFiles = info.files || [];
                            
                            if (glossarySwitch) {
                                if (glossaryEnabled) {
                                    glossarySwitch.classList.add('active');
                                } else {
                                    glossarySwitch.classList.remove('active');
                                }
                            }
                            
                            // 显示/隐藏文件列表
                            if (filesContainer) {
                                filesContainer.style.display = glossaryEnabled ? 'block' : 'none';
                            }
                            
                            renderGlossaryFiles(glossaryFiles);
                            updateGlossaryCount();
                        };
                        updateGlossaryUI();

                        // TM stats
                        (async () => {
                            const tmStats = await window.pywebview.api.get_tm_stats();
                            const el = document.getElementById('tm-stats');
                            if (el) {
                                if (tmStats.total === 0) {
                                    el.textContent = 'TM: empty | 记忆库为空';
                                } else {
                                    const topPair = tmStats.lang_pairs[0];
                                    const pairStr = topPair ? ` (${topPair.source}→${topPair.target})` : '';
                                    el.textContent = `TM: ${tmStats.total} segments${pairStr}`;
                                }
                            }
                        })();
                        document.getElementById('tm-clear-btn')?.addEventListener('click', async (e) => {
                            e.stopPropagation();
                            if (confirm('Clear all Translation Memory? | 清空所有翻译记忆？')) {
                                await window.pywebview.api.clear_tm();
                                const el = document.getElementById('tm-stats');
                                if (el) el.textContent = 'TM: empty | 记忆库已清空';
                            }
                        });

                        // 术语表总开关点击
                        document.getElementById('glossaryToggle')?.addEventListener('click', async (e) => {
                            // 阻止冒泡，避免触发文件项的点击
                            if (e.target.closest('.glossary-file-item')) return;
                            
                            glossaryEnabled = !glossaryEnabled;
                            await window.pywebview.api.set_glossary_enabled(glossaryEnabled);
                            
                            const glossarySwitch = document.getElementById('glossarySwitch');
                            const filesContainer = document.getElementById('glossary-files');
                            
                            if (glossarySwitch) {
                                if (glossaryEnabled) {
                                    glossarySwitch.classList.add('active');
                                } else {
                                    glossarySwitch.classList.remove('active');
                                }
                            }
                            
                            if (filesContainer) {
                                filesContainer.style.display = glossaryEnabled ? 'block' : 'none';
                            }
                            
                            updateGlossaryCount();
                            updateStatusInfo('Glossary | 术语表', glossaryEnabled ? 'Enabled | 已启用' : 'Disabled | 已禁用');
                        });
                        
                        const handleFileSelect = async (isBatch) => {
                            if (isFileDialogOpen) return;
                            isFileDialogOpen = true;
                            try {
                                const files = isBatch ? await window.pywebview.api.open_batch_file_dialog() : [await window.pywebview.api.open_file_dialog()];
                                if (files && files[0]) {
                                    settings.isBatch = isBatch;
                                    settings.glossaryEnabled = glossaryEnabled;
                                    if (isBatch) {
                                        settings.filePaths = files;
                                        updateStatusInfo('Batch Files | 批量文件', `${files.length} files selected | 已选择${files.length}个文件`);
                                    } else {
                                        settings.filePath = files[0];
                                        updateStatusInfo('File Selected | 文件已选择', files[0].split(/[\\\\/]/).pop());
                                    }
                                    renderState('LOADING', 'Analyzing document... | 分析文档...');
                                    const [hasExcel, sourceLang] = await Promise.all([
                                        window.pywebview.api.has_excel_files(isBatch ? files : [files[0]]),
                                        window.pywebview.api.analyze_language(files[0])
                                    ]);
                                    settings.sourceLang = sourceLang;
                                    updateStatusInfo('Language | 语言', `Detected: ${sourceLang}`);
                                    if (hasExcel) { renderState(isBatch ? 'BATCH_EXCEL_EXCLUDE' : 'EXCEL_EXCLUDE'); }
                                    else { renderState('CONFIRM_SOURCE'); }
                                }
                            } finally { isFileDialogOpen = false; }
                        };
                        document.getElementById('fileSelectBtn')?.addEventListener('click', () => handleFileSelect(false));
                        document.getElementById('batchSelectBtn')?.addEventListener('click', () => handleFileSelect(true));
                        document.getElementById('switchToProofreadBtn')?.addEventListener('click', () => renderState('PROOFREAD_SELECT'));
                        loadInitialData();
                        break;
                    case 'PROOFREAD_SELECT':
                        document.getElementById('proofreadFileBtn')?.addEventListener('click', async () => {
                            if (isFileDialogOpen) return;
                            isFileDialogOpen = true;
                            try {
                                const file = await window.pywebview.api.open_file_dialog();
                                if (file) {
                                    settings.filePath = file;
                                    settings.mode = 'proofread';
                                    updateStatusInfo('File Selected | 文件已选择', file.split(/[\\\\/]/).pop());
                                    renderState('PROOFREAD_LANG');
                                }
                            } finally { isFileDialogOpen = false; }
                        });
                        document.getElementById('switchToTranslateBtn')?.addEventListener('click', () => renderState('FILE_SELECT'));
                        loadInitialData();
                        break;
                    case 'PROOFREAD_LANG':
                        // 预设语言按钮
                        document.querySelectorAll('.btn-group .btn-primary[data-lang]').forEach(btn => {
                            btn.addEventListener('click', () => {
                                settings.proofreadLang = btn.dataset.lang;
                                renderState('PROOFREAD_OPTIONS');
                            });
                        });
                        // 自定义语言
                        document.getElementById('customLangBtn')?.addEventListener('click', () => {
                            const input = document.getElementById('customLangInput');
                            if (input && input.value.trim()) {
                                settings.proofreadLang = input.value.trim();
                                renderState('PROOFREAD_OPTIONS');
                            }
                        });
                        break;
                    case 'PROOFREAD_OPTIONS':
                        // 切换选项
                        document.querySelectorAll('.proofread-option').forEach(option => {
                            option.addEventListener('click', () => {
                                option.classList.toggle('selected');
                                const checkbox = option.querySelector('.proofread-checkbox');
                                const optionName = option.dataset.option;
                                proofreadOptions[optionName] = option.classList.contains('selected');
                                checkbox.textContent = proofreadOptions[optionName] ? '✓' : '';
                            });
                        });
                        // 开始校对
                        document.getElementById('startProofreadBtn')?.addEventListener('click', () => {
                            settings.proofreadOptions = { ...proofreadOptions };
                            renderState('PROOFREADING');
                            window.pywebview.api.start_proofreading(settings);
                        });
                        break;
                    case 'EXCEL_EXCLUDE':
                        setupInputHandler('excludeRule', 'excelContinueBtn', rule => { settings.excelExclude = rule; renderState('CONFIRM_SOURCE'); });
                        document.getElementById('excelSkipBtn')?.addEventListener('click', () => { settings.excelExclude = ''; renderState('CONFIRM_SOURCE'); });
                        break;
                    case 'BATCH_EXCEL_EXCLUDE':
                        setupInputHandler('batchExcludeRule', 'batchExcelContinueBtn', rule => { settings.batchExcelExclude = rule; renderState('CONFIRM_SOURCE'); });
                        document.getElementById('batchExcelSkipBtn')?.addEventListener('click', () => { settings.batchExcelExclude = ''; renderState('CONFIRM_SOURCE'); });
                        break;
                    case 'CONFIRM_SOURCE':
                        document.getElementById('sourceConfirmBtn')?.addEventListener('click', async () => { settings.targetLang = await window.pywebview.api.predict_target_language(); renderState('CONFIRM_TARGET'); });
                        document.getElementById('sourceDenyBtn')?.addEventListener('click', () => renderState('INPUT_SOURCE'));
                        break;
                    case 'INPUT_SOURCE':
                        setupInputHandler('sourceLangInput', 'sourceInputBtn', async lang => { settings.sourceLang = lang; settings.targetLang = await window.pywebview.api.predict_target_language(); renderState('CONFIRM_TARGET'); });
                        break;
                    case 'CONFIRM_TARGET':
                        document.getElementById('targetConfirmBtn')?.addEventListener('click', () => renderState('TRANSLATION_MODE'));
                        document.getElementById('targetDenyBtn')?.addEventListener('click', () => renderState('INPUT_TARGET'));
                        break;
                    case 'INPUT_TARGET':
                        setupInputHandler('targetLangInput', 'targetInputBtn', lang => {
                            settings.targetLang = lang;
                            renderState('TRANSLATION_MODE');
                        });
                        break;
                    case 'TRANSLATION_MODE':
                        const startWithMode = (mode) => {
                            settings.translationMode = mode;
                            renderState('ADVANCED_OPTIONS');
                        };
                        document.getElementById('replaceMode')?.addEventListener('click', () => startWithMode('replace'));
                        document.getElementById('appendMode')?.addEventListener('click', () => startWithMode('append'));
                        break;
                    case 'ADVANCED_OPTIONS':
                        document.querySelectorAll('#app .toggle-switch[data-switch]').forEach(sw => {
                            const key = sw.dataset.switch;
                            if (advOptions[key]) sw.classList.add('active');
                            else sw.classList.remove('active');
                            sw.closest('.glossary-toggle')?.addEventListener('click', () => {
                                advOptions[key] = !advOptions[key];
                                if (advOptions[key]) sw.classList.add('active');
                                else sw.classList.remove('active');
                            });
                        });
                        document.getElementById('advContinueBtn')?.addEventListener('click', () => {
                            settings.confidentialMode = advOptions.confidentialMode;
                            settings.fuzzyTM = advOptions.fuzzyTM;
                            settings.pivotThroughEnglish = advOptions.pivotThroughEnglish;
                            settings.contextInference = advOptions.contextInference;
                            settings.termArbitration = advOptions.termArbitration;
                            renderState('TRANSLATION_FORMALITY');
                        });
                        break;
                    case 'TRANSLATION_FORMALITY':
                        // Highlight selected formality option
                        document.querySelectorAll('.formality-option').forEach(opt => {
                            if (opt.dataset.formality === selectedFormality) opt.classList.add('selected');
                            else opt.classList.remove('selected');
                            opt.addEventListener('click', () => {
                                document.querySelectorAll('.formality-option').forEach(o => o.classList.remove('selected'));
                                opt.classList.add('selected');
                                selectedFormality = opt.dataset.formality;
                            });
                        });
                        document.getElementById('formalityConfirmBtn')?.addEventListener('click', () => {
                            settings.formality = selectedFormality;
                            if (settings.isBatch) { renderState('BATCH_TRANSLATING'); window.pywebview.api.start_batch_translation(settings); }
                            else { renderState('TRANSLATING'); window.pywebview.api.start_translation(settings); }
                        });
                        break;
                    case 'COMPLETE':
                        stopShredAnimation();
                        document.getElementById('anotherBtn')?.addEventListener('click', () => {
                            const apiKey = settings.apiKey;
                            settings = { apiKey };
                            stateHistory = [];
                            updateStatusInfo('Ready | 就绪', 'Ready for next translation | 准备下次翻译');
                            renderState('FILE_SELECT');
                        });
                        break;
                }
            };
            
            window.handle_message = (data) => {
                switch (data.type) {
                    case 'clear':
                        updateStatusInfo('Status | 状态', 'Starting... | 开始...');
                        break;
                    case 'status':
                        updateStatusInfo('Status | 状态', data.text);
                        appendProgressLog(data.text);
                        break;
                    case 'error':
                        updateStatusInfo('Error | 错误', data.text);
                        appendProgressLog('⚠ ' + data.text, 'error');
                        break;
                    case 'info':
                        updateStatusInfo('Info | 信息', data.text);
                        const isTM = data.text.includes('TM');
                        appendProgressLog(data.text, isTM ? 'tm-hit' : 'info');
                        break;
                    case 'progress': {
                        const label = document.getElementById('progress-label');
                        if (label) label.textContent = data.text;
                        updateStatusInfo('Progress | 进度', data.text);
                        appendProgressLog(data.text);
                        if (data.text && data.text.length > 5) {
                            createTextCard(data.text.substring(0, 36) + (data.text.length > 36 ? '...' : ''));
                            spawnCharBits(data.text);  // 批量模式：字符从前牌向上飘飞
                        }
                        break;
                    }
                    case 'batch_progress': {
                        const fileNum = document.getElementById('file-num');
                        const currentLabel = document.getElementById('batch-current');
                        if (fileNum && currentLabel) {
                            const [progress, filename] = data.text.split('|');
                            fileNum.textContent = progress;
                            currentLabel.textContent = filename;
                            updateStatusInfo('Batch Progress', `${progress} - ${filename}`);
                            appendProgressLog(`[${progress}] ${filename}`);
                            // 兜底：进入第n个文件时，确保前n-1张牌已飞出（即使file_done事件丢失）
                            const n = parseInt(progress.split('/')[0], 10);
                            if (!isNaN(n)) {
                                let flown = batchCards.filter(c => c.done || c.flying).length;
                                while (flown < n - 1) { flyOutCard(); flown++; }
                            }
                        }
                        break;
                    }
                    case 'file_done': {
                        // 单个文档翻译完成：对应的牌飞向角落消失
                        flyOutCard(data.text);
                        appendProgressLog(`✓ ${data.text}`, 'tm-hit');
                        break;
                    }
                }
            };
            
            window.showCompletion = (message) => {
                stopShredAnimation();
                const pending = batchCards.filter(c => !c.done && !c.flying).length;
                if (pending > 0) {
                    // 让剩余的牌依次飞出后再切换完成页
                    flyOutRemainingCards();
                    setTimeout(() => {
                        updateStatusInfo('Complete | 完成', 'Translation finished | 翻译完成');
                        renderState('COMPLETE', message);
                    }, pending * 150 + 900);
                } else {
                    updateStatusInfo('Complete | 完成', 'Translation finished | 翻译完成');
                    renderState('COMPLETE', message);
                }
            };
            
            window.addEventListener('pywebviewready', () => {
                loadInitialData();
                renderState('INIT');
                setTimeout(() => { window.pywebview.api.show_window(); }, 100);
            });
        });
    </script>
</body>
</html>'''

def main():
    """主函数"""
    global _background_loader, WINDOW, POETRY_QUOTE, TERMS_STATUS
    
    print("🚀 Initializing Sentient Translator (Enhanced)...")
    
    # 立即启动后台加载器
    _background_loader = BackgroundLoader()
    _background_loader.start_loading()
    print("📚 Background loading started...")
    
    # 立即获取备用诗句
    POETRY_QUOTE = _background_loader.get_poetry(timeout=0.5)
    print(f"🎨 Poetry loaded: {POETRY_QUOTE}")
    
    # 等待并获取terms加载结果
    terms_result = _background_loader.get_terms(timeout=5)
    TERMS_STATUS = terms_result
    term_map, term_re, terms_msg = terms_result
    print(f"📖 Terms status: {len(term_map)} terms loaded")
    print(terms_msg)
    
    # 创建API实例
    api = Api()
    print("⚡ API ready...")
    
    html_content = get_html_content()
    
    try:
        print("🎨 Creating window...")
        WINDOW = webview.create_window(
            'Sentient Translator (Enhanced)', 
            html=html_content, 
            js_api=api, 
            width=550, 
            height=620, 
            resizable=False, 
            frameless=True, 
            easy_drag=True, 
            hidden=True, 
            background_color='#2c2c2e'
        )
        
        print("🔧 Setting up tray...")
        tray_thread = threading.Thread(target=setup_tray, args=(WINDOW,))
        tray_thread.daemon = True
        tray_thread.start()
        
        print("✅ Ready! Starting webview...")
        webview.start()
        
    except Exception as e:
        print(f"❌ Application error: {e}")
    finally:
        if _background_loader:
            _background_loader.shutdown()


if __name__ == "__main__":
    main()