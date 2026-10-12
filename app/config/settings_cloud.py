from pathlib import Path
import os
import hashlib
from urllib.parse import urlsplit, unquote
from django.core.exceptions import ImproperlyConfigured

BASE_DIR=Path(__file__).resolve().parents[2]
raw_secret = os.environ.get('DJANGO_SECRET_KEY', '')
if len(raw_secret) < 43:
    raise ImproperlyConfigured('DJANGO_SECRET_KEY must be a random secret of at least 43 characters.')
SECRET_KEY = hashlib.sha256(raw_secret.encode('utf8')).hexdigest()
parsed = urlsplit(os.environ.get('DATABASE_URL', ''))
if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname or not parsed.path.strip('/'):
    raise ImproperlyConfigured('DATABASE_URL must be a PostgreSQL connection URL.')
hosts = os.environ.get('DJANGO_ALLOWED_HOSTS', os.environ.get('RENDER_EXTERNAL_HOSTNAME', ''))

DEBUG=False
ALLOWED_HOSTS = [x.strip() for x in hosts.split(',') if x.strip()]
if not ALLOWED_HOSTS or '*' in ALLOWED_HOSTS:
    raise ImproperlyConfigured('Set explicit DJANGO_ALLOWED_HOSTS.')
CSRF_TRUSTED_ORIGINS = [x.strip() for x in os.environ.get('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if x.strip()]
CSRF_TRUSTED_ORIGINS += ['https://' + h for h in ALLOWED_HOSTS if not h.startswith('.') and ':' not in h]
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if os.environ.get('DJANGO_TRUST_PROXY', '0') == '1' else None
SECURE_SSL_REDIRECT = os.environ.get('DJANGO_HTTPS', '1') == '1'
SESSION_COOKIE_SECURE = SECURE_SSL_REDIRECT
CSRF_COOKIE_SECURE = SECURE_SSL_REDIRECT
SECURE_HSTS_SECONDS = 3600 if SECURE_SSL_REDIRECT else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
SECURE_CONTENT_TYPE_NOSNIFF = True
INSTALLED_APPS=['django.contrib.auth','django.contrib.contenttypes','django.contrib.sessions','django.contrib.messages','django.contrib.staticfiles',
                'django.contrib.postgres','domain']
MIDDLEWARE=['django.middleware.security.SecurityMiddleware',
 'whitenoise.middleware.WhiteNoiseMiddleware',
 'django.contrib.sessions.middleware.SessionMiddleware','django.middleware.common.CommonMiddleware',
 'django.middleware.csrf.CsrfViewMiddleware','django.contrib.auth.middleware.AuthenticationMiddleware',
 'django.contrib.messages.middleware.MessageMiddleware','django.middleware.clickjacking.XFrameOptionsMiddleware']
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.postgresql',
    'NAME': unquote(parsed.path.lstrip('/')),
    'USER': unquote(parsed.username or ''),
    'PASSWORD': unquote(parsed.password or ''),
    'HOST': parsed.hostname,
    'PORT': parsed.port or 5432,
    'CONN_MAX_AGE': 60,
    'OPTIONS': {'sslmode': os.environ.get('DB_SSLMODE', 'require'), 'connect_timeout': 5},
}}
AUTH_USER_MODEL='domain.User'
AUTH_PASSWORD_VALIDATORS=[
 {'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
 {'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator'},
 {'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},
 {'NAME':'django.contrib.auth.password_validation.NumericPasswordValidator'}]
LANGUAGE_CODE='zh-hans'
TIME_ZONE='Asia/Shanghai'
USE_TZ=True
ROOT_URLCONF='config.urls'
DEFAULT_AUTO_FIELD='django.db.models.BigAutoField'
TEMPLATES=[{'BACKEND':'django.template.backends.django.DjangoTemplates',
 'DIRS':[BASE_DIR/'app/templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':[
 'django.template.context_processors.request','django.contrib.auth.context_processors.auth',
 'django.contrib.messages.context_processors.messages','domain.views.layout']}}]
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE='Lax'
CSRF_COOKIE_SAMESITE='Lax'
STATIC_URL='/static/'
STATIC_ROOT=BASE_DIR/'.local/static'
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage'},
}
WSGI_APPLICATION = 'config.wsgi.application'
LOGIN_URL='/login/'
LOGIN_REDIRECT_URL='/'
LOGOUT_REDIRECT_URL='/login/'
SESSION_COOKIE_AGE=28800
SESSION_EXPIRE_AT_BROWSER_CLOSE=True
