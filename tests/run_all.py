"""一键运行全部测试：python tests/run_all.py"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = ['test_logic.py', 'test_word_integration.py', 'test_ppt_integration.py',
         'test_excel_integration.py']

failed = 0
for f in FILES:
    print(f'\n===== {f} =====')
    r = subprocess.run([sys.executable, os.path.join(HERE, f)])
    if r.returncode != 0:
        failed += 1
print(f'\n{"❌ " + str(failed) + " file(s) failed" if failed else "✅ all test files passed"}')
sys.exit(1 if failed else 0)
