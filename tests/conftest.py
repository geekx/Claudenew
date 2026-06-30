"""
共享测试夹具：把重型 GUI/Office 依赖打桩，使 NCAT_Smart_V3.py 可在无显示环境导入。

- `load_ncat(use_real_docx=False)`：导入主模块。
  use_real_docx=True 时不打桩 docx，用真实 python-docx 做端到端集成测试。
- 关键点：openpyxl 的 Cell 桩必须是「独立的占位类」而非 object，
  否则 update_element_text 里的 isinstance(x, Cell) 会对任何对象都为真。
"""
import sys
import types
import importlib.util
import os

NCAT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         'NCAT_Smart_V3.py')


def _stub(name, **attrs):
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


def install_stubs(use_real_docx=False, use_real_pptx=False, use_real_openpyxl=False):
    _stub('pytz', timezone=lambda *a, **k: types.SimpleNamespace(zone='Asia/Shanghai'))
    _stub('requests', Session=object,
          exceptions=types.SimpleNamespace(Timeout=Exception, ConnectionError=Exception),
          post=lambda *a, **k: None)
    _stub('PIL'); _stub('PIL.Image'); _stub('PIL.ImageDraw'); _stub('PIL.ImageFont')
    sys.modules['PIL'].Image = sys.modules['PIL.Image']
    sys.modules['PIL'].ImageDraw = sys.modules['PIL.ImageDraw']
    sys.modules['PIL'].ImageFont = sys.modules['PIL.ImageFont']
    _stub('pystray', Icon=object, MenuItem=object)
    _stub('pandas', read_csv=lambda *a, **k: None)
    if not use_real_openpyxl:
        # Cell 用独立占位类，绝不能用 object
        _stub('openpyxl',
              cell=types.SimpleNamespace(cell=types.SimpleNamespace(Cell=type('Cell', (), {}))),
              utils=types.SimpleNamespace(column_index_from_string=lambda s: 1))
    _stub('webview', FileDialog=types.SimpleNamespace(OPEN=1),
          create_window=lambda *a, **k: None, start=lambda *a, **k: None)
    if not use_real_pptx:
        _stub('pptx', Presentation=object)
        _stub('pptx.util', Pt=lambda x: x, Emu=lambda x: x)
        _stub('pptx.enum'); _stub('pptx.enum.shapes', MSO_SHAPE_TYPE=types.SimpleNamespace(GROUP=6))
        _stub('pptx.dml'); _stub('pptx.dml.color', RGBColor=lambda *a: None)
    if not use_real_docx:
        _stub('docx', Document=object,
              text=types.SimpleNamespace(paragraph=types.SimpleNamespace(Paragraph=object)),
              enum=types.SimpleNamespace(text=types.SimpleNamespace(
                  WD_COLOR_INDEX=types.SimpleNamespace(YELLOW=1))))


def load_ncat(use_real_docx=False, use_real_pptx=False, use_real_openpyxl=False):
    install_stubs(use_real_docx=use_real_docx, use_real_pptx=use_real_pptx,
                  use_real_openpyxl=use_real_openpyxl)
    spec = importlib.util.spec_from_file_location('ncat', NCAT_PATH)
    ncat = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ncat)
    return ncat
