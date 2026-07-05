"""
纯逻辑单元测试（依赖打桩，无需真实 Office 库）。
覆盖：TM 模糊命中/迁移/防污染、语言判定、小语种英文中转、机密切碎、
脑补上下文、译法仲裁、防漏译二分递归、可译性判定。

运行：python tests/test_logic.py
"""
import os
import sys
import tempfile
import types
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from conftest import load_ncat

ncat = load_ncat(use_real_docx=False)


class CB:
    """收集 error 的进度回调桩"""
    def __init__(self): self.errors = []
    def progress(self, m): pass
    def status(self, m): pass
    def info(self, m): pass
    def error(self, m): self.errors.append(m)


def test_normalize_and_minor_lang():
    assert ncat.normalize_tm_key('Hello World') == ncat.normalize_tm_key(' hello   world. ')
    assert ncat.is_minor_language_target('Chinese', 'German') is True
    assert ncat.is_minor_language_target('Chinese', 'English') is False
    assert ncat.is_minor_language_target('Chinese', 'Chinese (Simplified)') is False
    h = ncat.build_context_hint(['句子一', '句子二', '句子一'], max_chars=50)
    assert '句子一' in h and h.count('句子一') == 1
    print('test_normalize_and_minor_lang: PASS')


def test_tm_fuzzy_and_migration():
    tmp = tempfile.mkdtemp()
    tm = ncat.TranslationMemory(os.path.join(tmp, 'tm.db'))
    tm.store('机密文件', 'Confidential document', 'chinese', 'english')
    assert tm.lookup('机密文件', 'chinese', 'english') == 'Confidential document'
    assert tm.lookup('  机密文件。 ', 'chinese', 'english', fuzzy=True) == 'Confidential document'
    assert tm.lookup('  机密文件。 ', 'chinese', 'english', fuzzy=False) is None
    # 旧库迁移
    oldp = os.path.join(tmp, 'old.db')
    c = sqlite3.connect(oldp)
    c.execute('CREATE TABLE tm (source_text TEXT NOT NULL, target_text TEXT NOT NULL, '
              'source_lang TEXT NOT NULL, target_lang TEXT NOT NULL, use_count INTEGER DEFAULT 1, '
              'created_at TEXT, updated_at TEXT, PRIMARY KEY (source_text, source_lang, target_lang))')
    c.execute("INSERT INTO tm (source_text,target_text,source_lang,target_lang) VALUES ('旧词','OldTerm','chinese','english')")
    c.commit(); c.close()
    tm2 = ncat.TranslationMemory(oldp)
    assert tm2.lookup(' 旧词 ', 'chinese', 'english', fuzzy=True) == 'OldTerm'
    print('test_tm_fuzzy_and_migration: PASS')


def test_tm_pollution_filter_and_purge():
    tmp = tempfile.mkdtemp()
    tm = ncat.TranslationMemory(os.path.join(tmp, 'tm.db'))
    tm.store('原文未译', '原文未译', 'chinese', 'german')               # 应被拒
    tm.store_batch([('甲', '甲'), ('乙', 'B-translated')], 'chinese', 'german')
    assert tm.lookup('原文未译', 'chinese', 'german') is None
    assert tm.lookup('甲', 'chinese', 'german') is None
    assert tm.lookup('乙', 'chinese', 'german') == 'B-translated'
    # purge：日文库里混入的纯中文长句应被清掉
    tmp2 = tempfile.mkdtemp()
    tmJ = ncat.TranslationMemory(os.path.join(tmp2, 'p.db'))
    tmJ.store('第三段中文', '另一段中文长句完全没有任何假名所以判为污染数据', 'chinese', 'japanese')
    tmJ.store('防火墙设置', 'ファイアウォールの設定', 'chinese', 'japanese')
    assert tmJ.purge_invalid() == 1
    assert tmJ.lookup('防火墙设置', 'chinese', 'japanese') == 'ファイアウォールの設定'
    print('test_tm_pollution_filter_and_purge: PASS')


def test_target_language_validation():
    f = ncat.tm_matches_target_language
    assert f('Die CPU läuft heiß', 'German') is True
    assert f('这是中文译文', 'German') is False
    assert f('ファイアウォール設定', 'German') is False
    assert f('这是中文', 'Chinese (Simplified)') is True
    assert f('This is English only text here', 'Chinese (Simplified)') is False
    assert f('ファイアウォールの設定を確認', 'Japanese') is True
    assert f('設定確認', 'Japanese') is True
    assert f('本段落为完全的中文内容并且字数明显超过八个汉字应判为污染', 'Japanese') is False
    assert f('방화벽 설정', 'Korean') is True
    print('test_target_language_validation: PASS')


def test_source_language_local_detection():
    g = ncat.detect_source_language_locally
    assert g(['这是一份关于网络安全的机密文档，请勿外传，内容包括防火墙配置。']) == 'Chinese (Simplified)'
    assert g(['ファイアウォールの設定を確認してください。これは機密文書です。']) == 'Japanese'
    assert g(['방화벽 설정을 확인하십시오. 이것은 기밀 문서입니다.']) == 'Korean'
    assert g(['This is a confidential document about network security.']) is None
    assert g(['123', '!!']) is None
    print('test_source_language_local_detection: PASS')


def test_translatable_detection():
    g = ncat.text_needs_translation_output
    assert g('这是中文') is True
    assert g('hello world') is True
    assert g('ABC-123') is False
    assert g('CPU') is False
    assert g('2024') is False
    assert g('A') is False
    print('test_translatable_detection: PASS')


def test_translate_pipeline_pivot_confidential_context():
    class Fake:
        def __init__(self): self.calls = []
        def translate_batch(self, texts, s, t, formality='auto', context_hint=''):
            self.calls.append({'src': s, 'tgt': t, 'n': len(texts), 'ctx': bool(context_hint)})
            return [f'[{s}->{t}]{x}' for x in texts]
    # 中->德：经英文中转两段式
    ft = Fake()
    res = ncat.translate_unique_texts(ft, ['甲', '乙'], 'Chinese', 'German', 'auto',
            {'confidential': False, 'pivot': True, 'contextInference': False}, CB(), need_capitalize=True)
    pairs = [(c['src'], c['tgt']) for c in ft.calls]
    assert ('Chinese', 'English') in pairs and ('English', 'German') in pairs
    assert res['甲'].count('->') == 2
    # 机密：小批次
    ncat.DEFAULT_CONFIG['CONFIDENTIAL_BATCH_SIZE'] = 2
    ft = Fake()
    res = ncat.translate_unique_texts(ft, [f'seg{i}' for i in range(5)], 'Chinese', 'English', 'auto',
            {'confidential': True, 'pivot': False, 'contextInference': False}, CB(), need_capitalize=False)
    assert max(c['n'] for c in ft.calls) <= 2 and len(res) == 5
    # 脑补上下文透传
    ft = Fake()
    ncat.translate_unique_texts(ft, ['x'], 'Chinese', 'English', 'auto',
            {'confidential': False, 'pivot': False, 'contextInference': True, 'contextHint': 'ctx'},
            CB(), need_capitalize=False)
    assert ft.calls[0]['ctx'] is True
    print('test_translate_pipeline_pivot_confidential_context: PASS')


def test_term_arbitration():
    import re
    import tempfile
    class Arb:
        def arbitrate_terms(self, texts, s, t, existing=None):
            return {'量子比特': 'qubit', '纠缠态': 'entangled state'}
    tm_map = {'已知词': 'known'}
    tm_re = re.compile('已知词')
    m2, r2 = ncat.maybe_arbitrate_terms(['量子比特'], tm_map, tm_re, Arb(),
            'Chinese', 'English', {'termArbitration': False}, CB())
    assert m2 is tm_map
    old = ncat.DEFAULT_CONFIG['TERMS_DIR']
    ncat.DEFAULT_CONFIG['TERMS_DIR'] = tempfile.mkdtemp()  # 别把仲裁术语写进仓库 terms/
    try:
        m3, r3 = ncat.maybe_arbitrate_terms(['量子比特和纠缠态'], tm_map, tm_re, Arb(),
                'Chinese', 'English', {'termArbitration': True}, CB())
    finally:
        ncat.DEFAULT_CONFIG['TERMS_DIR'] = old
    assert m3['量子比特'] == 'qubit' and m3['已知词'] == 'known' and r3.search('用量子比特')
    print('test_term_arbitration: PASS')


def test_bisection_zero_drop():
    """分隔符被吞导致段数不匹配时，二分递归保证零漏译、零错位。"""
    real = ncat.DeepSeekTranslator(api_key='x', temperature=1.0)
    sep = ncat.DEFAULT_CONFIG['TERM_SEPARATOR']

    def fake_post(*a, **k):
        payload = k.get('json') or a[0]
        texts = payload['messages'][-1]['content'].split(sep)

        class R:
            status_code = 200
            def raise_for_status(self): pass
            def json(self):
                if len(texts) > 1:
                    out = sep.join('訳:' + x for x in texts[:-1])  # 故意少一段
                else:
                    out = '訳:' + texts[0]
                return {'choices': [{'message': {'content': out}}]}
        return R()

    real.session = types.SimpleNamespace(post=fake_post)
    src = [f'第{i}段中文内容需要翻译' for i in range(13)]
    out = real.translate_batch(src, 'Chinese', 'Japanese')
    assert len(out) == len(src)
    for s, o in zip(src, out):
        assert o == '訳:' + s
    print('test_bisection_zero_drop: PASS')


def test_tm_export_import_roundtrip():
    """TM 导出→导入往返；导入时过滤污染行（译文=原文 / 语言不符）。"""
    import tempfile
    d = tempfile.mkdtemp()
    tm = ncat.TranslationMemory(os.path.join(d, 'tm.db'))
    tm.store('网络安全', 'ネットワークセキュリティ', 'chinese', 'japanese')
    tm.store('机密文件', 'Confidential document', 'chinese', 'english')
    csv_path = os.path.join(d, 'export.csv')
    assert tm.export_csv(csv_path) == 2

    # 导入到一个全新的库，应完整还原
    tm2 = ncat.TranslationMemory(os.path.join(d, 'tm2.db'))
    imported, skipped = tm2.import_csv(csv_path, validate=True)
    assert imported == 2 and skipped == 0
    assert tm2.lookup('网络安全', 'chinese', 'japanese') == 'ネットワークセキュリティ'
    assert tm2.lookup('机密文件', 'chinese', 'english') == 'Confidential document'

    # 构造带污染行的 CSV：译文=原文、日文列却是纯中文 → 导入时被跳过
    bad = os.path.join(d, 'bad.csv')
    with open(bad, 'w', encoding='utf-8-sig', newline='') as f:
        f.write('source,target,source_lang,target_lang\n')
        f.write('原样,原样,chinese,japanese\n')          # 译文=原文
        f.write('这段,完全是中文没有假名,chinese,japanese\n')  # 日文目标却纯中文
        f.write('数据,データ,chinese,japanese\n')          # 合法
    tm3 = ncat.TranslationMemory(os.path.join(d, 'tm3.db'))
    imp, skp = tm3.import_csv(bad, validate=True)
    assert imp == 1 and skp == 2, (imp, skp)
    assert tm3.lookup('数据', 'chinese', 'japanese') == 'データ'
    print('test_tm_export_import_roundtrip: PASS')


def test_save_glossary_csv_merge():
    import tempfile
    import csv as _csv
    d = tempfile.mkdtemp()
    p = os.path.join(d, 'g.csv')
    total, added = ncat.save_glossary_csv({'量子比特': 'qubit', '纠缠态': 'entangled state'}, p)
    assert (total, added) == (2, 2)
    # 合并：一个重复(覆盖) + 一个新增
    total, added = ncat.save_glossary_csv({'量子比特': 'qubit', '超导': 'superconducting'}, p)
    assert total == 3 and added == 1
    rows = {}
    with open(p, encoding='utf-8-sig') as f:
        for r in _csv.DictReader(f):
            rows[r['source']] = r['target']
    assert rows == {'量子比特': 'qubit', '纠缠态': 'entangled state', '超导': 'superconducting'}
    assert ncat._sanitize_lang_for_filename('Chinese (Simplified)') == 'chinese_simplified'
    print('test_save_glossary_csv_merge: PASS')


def test_arbitration_persists_glossary():
    import tempfile
    terms_dir = tempfile.mkdtemp()
    old = ncat.DEFAULT_CONFIG['TERMS_DIR']
    ncat.DEFAULT_CONFIG['TERMS_DIR'] = terms_dir  # 绝对路径，重定向到临时目录
    try:
        class Arb:
            def arbitrate_terms(self, texts, s, t, existing=None):
                return {'防火墙': 'firewall', '入侵检测': 'intrusion detection'}
        m, r = ncat.maybe_arbitrate_terms(['防火墙与入侵检测'], {}, None, Arb(),
                'Chinese', 'German', {'termArbitration': True, 'pivot': True}, CB())
        # 小语种(德语)+pivot → 仲裁目标为英文，落到 arbitrated_english.csv
        out = os.path.join(terms_dir, 'arbitrated_english.csv')
        assert os.path.exists(out), '仲裁术语未沉淀'
        import csv as _csv
        with open(out, encoding='utf-8-sig') as f:
            saved = {row['source']: row['target'] for row in _csv.DictReader(f)}
        assert saved.get('防火墙') == 'firewall' and saved.get('入侵检测') == 'intrusion detection'
        print('test_arbitration_persists_glossary: PASS')
    finally:
        ncat.DEFAULT_CONFIG['TERMS_DIR'] = old


def test_batch_resume_skips_completed():
    """批处理断点续跳：输出已存在的文件应被跳过，不再调用翻译流程。"""
    import tempfile
    import time as _time

    class BatchCB:
        def __init__(self): self.msgs = []
        def progress(self, m): pass
        def status(self, m): pass
        def info(self, m): self.msgs.append(m)
        def error(self, m): self.msgs.append(m)
        def batch_progress(self, c, t, f): pass
        def file_done(self, f): pass

    d = tempfile.mkdtemp()
    srcs = []
    for name in ('a', 'b', 'c'):
        p = os.path.join(d, f'{name}.docx')
        with open(p, 'w') as f:
            f.write('x')
        srcs.append(p)
    # 预置 a 的输出（--OP），且比源文件新 → 应被跳过
    done_out = ncat.compute_output_path(srcs[0], 'replace')
    _time.sleep(0.01)
    with open(done_out, 'w') as f:
        f.write('translated')

    assert ncat.is_batch_file_already_done(srcs[0], 'replace') is True
    assert ncat.is_batch_file_already_done(srcs[1], 'replace') is False

    calls = []
    orig = ncat.run_translation_process
    def fake_run(settings, cb, is_batch=False, file_index=0, total_files=0):
        calls.append(settings.get('currentFile'))
        with open(ncat.compute_output_path(settings['currentFile'], 'replace'), 'w') as f:
            f.write('translated')
        return True
    ncat.run_translation_process = fake_run
    try:
        cb = BatchCB()
        ncat.run_batch_translation(
            {'filePaths': srcs, 'translationMode': 'replace', 'skipCompleted': True, 'apiKey': None}, cb)
    finally:
        ncat.run_translation_process = orig

    # 只应翻译 b、c；a 被跳过
    assert calls == [srcs[1], srcs[2]], calls
    assert any('skipped 1' in m or '跳过1' in m for m in cb.msgs)
    print('test_batch_resume_skips_completed: PASS')


if __name__ == '__main__':
    fns = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for fn in fns:
        fn()
    print(f'\n✅ ALL {len(fns)} logic tests passed')
