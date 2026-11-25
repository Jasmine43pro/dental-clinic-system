"""Start the existing local DB and Django UI; all writable state stays in this project."""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
os.environ['TEMP'] = os.environ['TMP'] = str(ROOT/'.tmp')
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['PYTHONUTF8'] = '1'

if __name__ == '__main__':
    port = int(os.environ.get('DENTAL_WEB_PORT', '8000'))
    subprocess.run([sys.executable, '-B', str(ROOT/'scripts/database.py'), 'init'], cwd=ROOT, check=True)
    subprocess.run([sys.executable, '-B', str(ROOT/'app/manage.py'), 'check'], cwd=ROOT, check=True)
    for command in ('migrate', 'seed_demo'):
        subprocess.run([sys.executable, '-B', str(ROOT/'app/manage.py'), command], cwd=ROOT, check=True)
    print(f'打开 http://127.0.0.1:{port}/ 。按 Ctrl+C 停止网页服务。', flush=True)
    subprocess.run([sys.executable, '-B', str(ROOT/'app/manage.py'), 'runserver',
                    f'127.0.0.1:{port}', '--noreload', '--insecure'], cwd=ROOT, check=True)
