#!/usr/bin/env python3
"""Create/validate deployment configuration without printing secrets or executing .env."""
import argparse
import base64
import ipaddress
import os
from pathlib import Path
import re
import secrets
import shlex
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def read_env(path):
    result = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        key, separator, value = line.partition('=')
        if not separator or not re.fullmatch(r'[A-Z][A-Z0-9_]*',key.strip()):
            raise ValueError('Invalid .env line; use KEY=value syntax.')
        try:
            parts = shlex.split(value,comments=True)
        except ValueError:
            raise ValueError('Invalid quoting in .env.') from None
        if len(parts) > 1:
            raise ValueError('Quote values containing spaces in .env.')
        result[key.strip()] = parts[0] if parts else ''
    return result


def validate_url(value):
    parsed = urlsplit(value)
    if parsed.scheme not in {'http','https'} or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {'','/'} or parsed.query or parsed.fragment:
        raise ValueError('Use an HTTP(S) base URL with no path, credentials, query or fragment.')
    if not re.fullmatch(r'[A-Za-z0-9.:-]+',parsed.hostname):
        raise ValueError('Use an ASCII hostname or IP address.')
    parsed.port  # Validate the port.
    return parsed


def create(path, url, bind, port, import_env=None):
    parsed = validate_url(url)
    ipaddress.ip_address(bind)
    if not 1 <= port <= 65535:
        raise ValueError('Port must be between 1 and 65535.')
    values = {'SECRET_KEY':secrets.token_urlsafe(48),
              'CREDENTIAL_KEY':base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
              'DEBUG':'false','APP_URL':url.rstrip('/'),
              'ALLOWED_HOSTS':','.join(dict.fromkeys(['localhost','127.0.0.1',parsed.hostname])),
              'CSRF_TRUSTED_ORIGINS':url.rstrip('/'),'COOKIE_SECURE':str(parsed.scheme=='https').lower(),
              'BIND_ADDRESS':bind,'PORT':str(port)}
    if import_env:
        previous = read_env(Path(import_env))
        for key in ['SECRET_KEY','CREDENTIAL_KEY','TICKETMASTER_API_KEY','BRAVE_API_KEY']:
            if key in previous:
                values[key] = previous[key]
    output = []
    for line in (ROOT/'.env.example').read_text().splitlines():
        key = line.partition('=')[0]
        output.append(f'{key}={shlex.quote(values[key])}' if key in values else line)
    fd = os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as stream:
        stream.write('\n'.join(output)+'\n')


def validate(path):
    values = read_env(path)
    if values.get('DEBUG','').lower() != 'false':
        raise ValueError('Set DEBUG=false for deployment.')
    if len(values.get('SECRET_KEY','')) < 40:
        raise ValueError('SECRET_KEY must contain at least 40 characters.')
    try:
        if len(base64.urlsafe_b64decode(values.get('CREDENTIAL_KEY',''))) != 32:
            raise ValueError
    except (ValueError,TypeError):
        raise ValueError('CREDENTIAL_KEY must be a valid Fernet key; preserve it across updates.') from None
    parsed = validate_url(values.get('APP_URL',''))
    hosts = values.get('ALLOWED_HOSTS','').split(',')
    if not all(host in hosts for host in ['localhost','127.0.0.1',parsed.hostname]):
        raise ValueError('ALLOWED_HOSTS must include localhost, 127.0.0.1 and the APP_URL hostname.')
    if values.get('APP_URL','').rstrip('/') not in values.get('CSRF_TRUSTED_ORIGINS','').split(','):
        raise ValueError('CSRF_TRUSTED_ORIGINS must include APP_URL.')
    if parsed.scheme=='https' and values.get('COOKIE_SECURE','').lower()!='true':
        raise ValueError('Use COOKIE_SECURE=true with HTTPS.')
    if parsed.scheme=='http' and values.get('COOKIE_SECURE','').lower()!='false':
        raise ValueError('Use COOKIE_SECURE=false for private-LAN HTTP testing.')
    if not 1 <= int(values.get('PORT','8003')) <= 65535:
        raise ValueError('Invalid PORT.')
    ipaddress.ip_address(values.get('BIND_ADDRESS','127.0.0.1'))
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['create','validate','url'])
    parser.add_argument('--url')
    parser.add_argument('--bind',default='127.0.0.1')
    parser.add_argument('--port',type=int,default=8003)
    parser.add_argument('--import-env',type=Path)
    args = parser.parse_args()
    path = ROOT/'.env'
    try:
        if args.action=='create':
            if not args.url:
                parser.error('--url is required to create configuration')
            create(path,args.url,args.bind,args.port,args.import_env)
        else:
            values = validate(path)
            if args.action=='url':
                print(values['APP_URL'])
    except (ValueError,OSError) as exc:
        parser.exit(1,f'Configuration error: {exc}\n')

if __name__=='__main__':
    main()
