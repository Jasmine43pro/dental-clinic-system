"""Prepare cloud schema and static files; demo initialization is explicit."""
import os
from pathlib import Path
import subprocess
import sys
ROOT = Path(__file__).resolve().parents[1]
(ROOT/'.tmp').mkdir(exist_ok=True)
os.environ['TMP'] = os.environ['TEMP'] = str(ROOT/'.tmp')
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_cloud'

def manage(*args):
    subprocess.run([sys.executable, '-B', str(ROOT/'app/manage.py'), *args], cwd=ROOT, check=True)

if __name__ == '__main__':
    manage('migrate', '--noinput')
    manage('collectstatic', '--noinput')
    if os.environ.get('INITIALIZE_DEMO', '0') == '1':
        manage('seed_demo_cloud')
