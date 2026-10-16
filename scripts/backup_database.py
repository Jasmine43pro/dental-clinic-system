"""导出项目数据库，并在独立临时库中验证恢复；私有文件保存在项目D盘。"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
from urllib.parse import unquote, urlsplit

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / '.local' / 'backups'
TEMP = ROOT / '.tmp' / 'database-backup'
BJ = timezone(timedelta(hours=8))
SECRET_VALUES: list[str] = []


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def redact(text: str) -> str:
    for value in sorted(set(SECRET_VALUES), key=len, reverse=True):
        if value:
            text = text.replace(value, '[已隐藏]')
    return re.sub(r'postgres(?:ql)?://[^\s]+', '[数据库地址已隐藏]', text)


def local_parameters(database: str = 'dental_dev') -> dict:
    cfg = json.loads((ROOT / '.local' / 'config.json').read_text(encoding='utf-8'))
    SECRET_VALUES.extend([cfg['db_password'], cfg.get('bootstrap_password', ''), cfg.get('secret_key', '')])
    return dict(host='127.0.0.1', port=int(cfg['port']), user='dental_app',
                password=cfg['db_password'], dbname=database, sslmode='disable', connect_timeout=10)


def source_parameters(source: str) -> dict:
    if source == 'local':
        return local_parameters()
    raw = os.environ.get('DATABASE_URL', '')
    parsed = urlsplit(raw)
    if parsed.scheme not in ('postgres', 'postgresql') or not parsed.hostname or not parsed.path.strip('/'):
        raise ValueError('云端备份需要在本机进程环境中设置有效DATABASE_URL；不要把连接地址写进仓库。')
    SECRET_VALUES.extend([raw, unquote(parsed.password or '')])
    return dict(host=parsed.hostname, port=parsed.port or 5432, user=unquote(parsed.username or ''),
                password=unquote(parsed.password or ''), dbname=unquote(parsed.path.lstrip('/')),
                sslmode='require', connect_timeout=10)


def pg_environment(parameters: dict, *, read_only: bool = False) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith('PG')}
    env.update(TEMP=str(TEMP), TMP=str(TEMP), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')
    names = dict(host='PGHOST', port='PGPORT', user='PGUSER', password='PGPASSWORD',
                 dbname='PGDATABASE', sslmode='PGSSLMODE', connect_timeout='PGCONNECT_TIMEOUT')
    for key, name in names.items():
        env[name] = str(parameters[key])
    if read_only:
        env['PGOPTIONS'] = '-c default_transaction_read_only=on'
    return env


def pg_tools() -> Path:
    default = 'D:/PostgreSQL/18/bin' if os.name == 'nt' else ''
    path = Path(os.environ.get('PG_BIN', default)).resolve()
    for name in ('pg_dump', 'pg_restore'):
        executable = path / (name + ('.exe' if os.name == 'nt' else ''))
        if not executable.is_file():
            raise FileNotFoundError('找不到PostgreSQL导出恢复工具；通过PG_BIN指定已有工具的目录。')
    return path


def run_pg(bin_dir: Path, name: str, args: list[str], parameters: dict,
           log: Path, *, read_only: bool = False) -> None:
    executable = bin_dir / (name + ('.exe' if os.name == 'nt' else ''))
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run([str(executable), *args], env=pg_environment(parameters, read_only=read_only),
                            cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            encoding='utf-8', errors='replace', creationflags=flags)
    log.write_text(redact(result.stdout), encoding='utf-8')
    if result.returncode:
        raise RuntimeError(f'{name}未完成，退出码{result.returncode}；诊断见私有日志{log.name}。')


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def fingerprint(conn: psycopg.Connection) -> dict:
    """在同一快照中核对行内容与结构；结果只含哈希和数量，不含业务行。"""
    conn.execute("SET LOCAL TIME ZONE 'UTC'")
    tables = conn.execute("""
        SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relkind IN ('r','p') ORDER BY 1,2
    """).fetchall()
    data = {}
    for schema, table in tables:
        row_hashes = []
        statement = sql.SQL('SELECT row_to_json(t)::text FROM {} AS t').format(sql.Identifier(schema, table))
        for (row_text,) in conn.execute(statement):
            row_hashes.append(hashlib.sha256(row_text.encode('utf-8')).digest())
        data[f'{schema}.{table}'] = dict(rows=len(row_hashes),
                                       sha256=hashlib.sha256(b''.join(sorted(row_hashes))).hexdigest())
    structures = {}
    structures['columns'] = conn.execute("""
        SELECT c.relname,row_number() OVER (PARTITION BY c.oid ORDER BY a.attnum),
               a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,
               a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid)
        FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
        WHERE n.nspname='public' AND c.relkind IN ('r','p') AND a.attnum>0 AND NOT a.attisdropped
        ORDER BY c.relname,a.attnum
    """).fetchall()
    structures['constraints'] = conn.execute("""
        SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k
        JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' ORDER BY c.relname,k.conname
    """).fetchall()
    structures['indexes'] = conn.execute("""
        SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public'
        ORDER BY tablename,indexname
    """).fetchall()
    structures['triggers'] = conn.execute("""
        SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid) FROM pg_trigger t
        JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname
    """).fetchall()
    structures['functions'] = conn.execute("""
        SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid)
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        WHERE n.nspname='public' AND p.prokind='f'
          AND NOT EXISTS(SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass
                         AND d.objid=p.oid AND d.deptype='e') ORDER BY 1,2
    """).fetchall()
    structures['extensions'] = conn.execute('SELECT extname FROM pg_extension ORDER BY extname').fetchall()
    sequences = {}
    for (name,) in conn.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"):
        value = conn.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier('public', name))).fetchone()
        sequences[name] = dict(last_value=value[0], is_called=value[1])
    canonical = json.loads(json.dumps(structures, ensure_ascii=False))
    return dict(tables=data, sequences=sequences, structures=canonical)


def normalize_restored_definitions(conn: psycopg.Connection, expected: dict, actual: dict) -> tuple[dict, dict]:
    """由PostgreSQL重新解析表达式，核验等价DDL；不靠删除括号或强制类型转换忽略差异。"""
    normalized = json.loads(json.dumps(expected))
    changes = dict(check_definitions_reparsed=0, indexes_reparsed=0)
    target_constraints = {(row[0], row[1]): row[2] for row in actual['constraints']}
    for row in normalized['constraints']:
        table, name, definition = row
        target = target_constraints.get((table, name))
        if target is None or target == definition or not definition.startswith('CHECK '):
            continue
        temporary = 'backup_check_' + secrets.token_hex(6)
        conn.execute(sql.SQL('ALTER TABLE {} ADD CONSTRAINT {} {}').format(
            sql.Identifier('public', table), sql.Identifier(temporary), sql.SQL(definition)))
        try:
            row[2] = conn.execute("""
                SELECT pg_get_constraintdef(oid) FROM pg_constraint
                WHERE conrelid=%s::regclass AND conname=%s
            """, ('public.' + table, temporary)).fetchone()[0]
            changes['check_definitions_reparsed'] += 1
        finally:
            conn.execute(sql.SQL('ALTER TABLE {} DROP CONSTRAINT {}').format(
                sql.Identifier('public', table), sql.Identifier(temporary)))
    target_indexes = {(row[0], row[1]): row[2] for row in actual['indexes']}
    for row in normalized['indexes']:
        table, name, definition = row
        target = target_indexes.get((table, name))
        if target is None or target == definition:
            continue
        if not re.fullmatch(r'[a-z_][a-z0-9_]*', name):
            raise RuntimeError('索引定义需要人工核对：' + name)
        temporary = 'backup_index_' + secrets.token_hex(6)
        prefix = re.compile(r'^CREATE (UNIQUE )?INDEX ' + re.escape(name) + r' ON ')
        compiled, count = prefix.subn(lambda match: 'CREATE ' + (match.group(1) or '') +
                                    'INDEX ' + temporary + ' ON ', definition, count=1)
        if count != 1:
            raise RuntimeError('无法安全重新解析索引定义：' + name)
        conn.execute(compiled)
        try:
            canonical = conn.execute('SELECT pg_get_indexdef(%s::regclass)', ('public.' + temporary,)).fetchone()[0]
            row[2] = re.sub(r'^(CREATE (?:UNIQUE )?INDEX )' + temporary + r' ON ',
                            lambda match: match.group(1) + name + ' ON ', canonical, count=1)
            changes['indexes_reparsed'] += 1
        finally:
            conn.execute(sql.SQL('DROP INDEX {}').format(sql.Identifier('public', temporary)))
    return normalized, changes


def verify_archive(archive: Path, metadata: dict, bin_dir: Path) -> dict:
    """只恢复到本项目本机新建的临时库，绝不覆盖原库或云库。"""
    if file_sha256(archive) != metadata['archive_sha256']:
        raise ValueError('备份文件哈希不符，停止恢复。')
    name = 'dental_restorecheck_' + datetime.now(BJ).strftime('%Y%m%d%H%M%S') + '_' + secrets.token_hex(4)
    if not re.fullmatch(r'dental_restorecheck_[0-9]{14}_[0-9a-f]{8}', name):
        raise ValueError('临时数据库名不符合隔离规则。')
    target = local_parameters(name)
    maintenance = local_parameters('postgres')
    created = False
    result = dict(verified=False, source_database_modified=False, target='isolated_local_database',
                  temporary_database=name, temporary_database_removed=False)
    with psycopg.connect(**maintenance, autocommit=True) as control:
        try:
            control.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(name)))
            created = True
            run_pg(bin_dir, 'pg_restore', ['--no-owner', '--no-privileges', '--exit-on-error',
                                         '--dbname', name, str(archive)], target, archive.parent / 'restore-check.log')
            with psycopg.connect(**target, options='-c default_transaction_read_only=on') as restored:
                restored.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                actual = fingerprint(restored)
            expected = metadata['fingerprint']
            with psycopg.connect(**target) as normalizer:
                normalized, changes = normalize_restored_definitions(normalizer, expected['structures'], actual['structures'])
            mismatches = [key for key in ('tables', 'sequences') if actual[key] != expected[key]]
            if normalized != actual['structures']:
                mismatches.append('structures')
            if mismatches:
                write_json(archive.parent / 'restore-check-difference.json', dict(mismatched=mismatches, actual=actual))
                raise RuntimeError('恢复核对未通过：' + '、'.join(mismatches) + '。备份时请暂停新增录入后重新核验。')
            result.update(verified=True, checked_tables=len(actual['tables']),
                          checked_rows=sum(item['rows'] for item in actual['tables'].values()),
                          data_hashes_match=True, sequence_states_match=True, structures_match=True,
                          structure_normalizations=changes)
        finally:
            if created:
                control.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
                result['temporary_database_removed'] = True
    return result


def backup(source: str, verify: bool) -> Path:
    parameters = source_parameters(source)
    bin_dir = pg_tools()
    directory = PRIVATE / (datetime.now(BJ).strftime('%Y%m%d-%H%M%S') + '-' + secrets.token_hex(3))
    directory.mkdir(parents=True, exist_ok=False)
    archive = directory / 'database.dump'
    metadata_path = directory / 'backup.json'
    metadata = dict(format_version=1, created_at=datetime.now(BJ).isoformat(), source=source,
                    database=parameters['dbname'], archive='database.dump', source_read_only=True,
                    verification=dict(verified=False), state='exporting')
    write_json(metadata_path, metadata)
    try:
        with psycopg.connect(**parameters, options='-c default_transaction_read_only=on') as conn:
            conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
            metadata['postgres_version'] = conn.execute("SELECT current_setting('server_version')").fetchone()[0]
            snapshot = conn.execute('SELECT pg_export_snapshot()').fetchone()[0]
            metadata['fingerprint'] = fingerprint(conn)
            run_pg(bin_dir, 'pg_dump', ['--format=custom', '--snapshot=' + snapshot, '--file', str(archive)],
                   parameters, directory / 'export.log', read_only=True)
        metadata.update(archive_sha256=file_sha256(archive), archive_bytes=archive.stat().st_size, state='exported')
        write_json(metadata_path, metadata)
        if verify:
            metadata['verification'] = verify_archive(archive, metadata, bin_dir)
            metadata['state'] = 'verified'
            write_json(metadata_path, metadata)
        return directory
    except Exception as error:
        metadata.update(state='failed', error=redact(str(error)))
        write_json(metadata_path, metadata)
        raise


def main() -> int:
    if ROOT.drive.upper() != 'D:':
        raise RuntimeError('本项目备份必须在D盘运行，已停止。')
    PRIVATE.mkdir(parents=True, exist_ok=True)
    TEMP.mkdir(parents=True, exist_ok=True)
    parser = argparse.ArgumentParser(description='私有数据库备份与独立恢复核验；不修改原数据库。')
    sub = parser.add_subparsers(dest='action', required=True)
    export = sub.add_parser('backup', help='导出完整数据库')
    export.add_argument('--source', choices=('local', 'cloud'), default='local')
    export.add_argument('--verify', action='store_true', help='导出后恢复到本机独立临时库并核验')
    check = sub.add_parser('verify', help='重新验证已有备份；不覆盖原库')
    check.add_argument('directory', help='项目.local/backups下的备份目录')
    args = parser.parse_args()
    if args.action == 'backup':
        directory = backup(args.source, args.verify)
    else:
        directory = Path(args.directory).resolve()
        if not directory.is_relative_to(PRIVATE.resolve()):
            raise ValueError('仅接受本项目.local/backups内的私有备份目录。')
        metadata = json.loads((directory / 'backup.json').read_text(encoding='utf-8'))
        archive = directory / 'database.dump'
        metadata['verification'] = verify_archive(archive, metadata, pg_tools())
        metadata['state'] = 'verified'
        write_json(directory / 'backup.json', metadata)
    metadata = json.loads((directory / 'backup.json').read_text(encoding='utf-8'))
    print(json.dumps(dict(backup_directory=str(directory), archive_bytes=metadata['archive_bytes'],
                          archive_sha256=metadata['archive_sha256'], verification=metadata['verification']),
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    try:
        raise SystemExit(main())
    except Exception as error:
        print('备份未完成：' + redact(str(error)), file=sys.stderr)
        raise SystemExit(1)
