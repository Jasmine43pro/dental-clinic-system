"""Project-local dependencies only; no global changes or existing DB changes."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if ROOT.drive.upper() != 'D:':
    raise SystemExit('Project must be on D:')
for folder in ('.tmp', '.cache/pip', '.local', 'outputs'):
    (ROOT / folder).mkdir(parents=True, exist_ok=True)
env = dict(os.environ, TEMP=str(ROOT/'.tmp'), TMP=str(ROOT/'.tmp'),
           PIP_CACHE_DIR=str(ROOT/'.cache/pip'), PYTHONDONTWRITEBYTECODE='1',
           PYTHONUTF8='1', PIP_DISABLE_PIP_VERSION_CHECK='1')
python = ROOT / '.venv/Scripts/python.exe'
def run(args):
    subprocess.run([str(x) for x in args], cwd=ROOT, env=env, check=True)
if not python.exists():
    run([sys.executable, '-B', '-m', 'venv', ROOT/'.venv'])
if '--install' in sys.argv:
    source = ROOT/'requirements.lock.txt'
    if not source.exists():
        source = ROOT/'requirements.in'
    run([python, '-B', '-m', 'pip', 'install', '-r', source])
    run([python, '-B', '-m', 'pip', 'check'])
print('Project environment ready at', python)
