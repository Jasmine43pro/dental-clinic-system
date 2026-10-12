"""Start the Linux WSGI service using the platform-provided port."""
import os
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_cloud'
port = int(os.environ.get('PORT', '8000'))
if not 1 <= port <= 65535:
    raise SystemExit('PORT is outside the valid range.')
os.execvp('gunicorn', ['gunicorn', '--chdir', str(ROOT/'app'), 'config.wsgi:application',
    '--bind', '0.0.0.0:' + str(port), '--workers', '2', '--timeout', '60',
    '--access-logfile', '-', '--error-logfile', '-'])
