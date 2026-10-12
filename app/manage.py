import os
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('TEMP',str(ROOT/'.tmp'))
os.environ['TEMP']=os.environ['TMP']=str(ROOT/'.tmp')
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
if __name__=='__main__':
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)
