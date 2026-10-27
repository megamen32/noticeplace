#!/usr/bin/env python3
"""Synchronize the existing Herder health bearer into protected job profiles.

Run as root on server-100. No secret is accepted on argv or printed. The
NoticePlace EnvironmentFile supplies the existing credential. Restart Herder
after synchronization so its bearer verifier reads the same value.
"""
import json
import os
from pathlib import Path
import shlex
import stat
import tempfile


def main():
    source = Path('/etc/notification-center.env')
    info = source.stat()
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise RuntimeError('NoticePlace environment must be private')
    tokens = [shlex.split(line.split('=', 1)[1])[0] for line in
              source.read_text().splitlines()
              if line.startswith('NOTICEPLACE_HEALTH_LAUNCH_TOKEN=')]
    if len(tokens) != 1 or len(tokens[0]) < 32:
        raise RuntimeError('Expected one existing strong health-launch credential')
    herder = Path('/home/roomhacker/.config/agent-herder/herder.env')
    info = herder.stat()
    if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != 1000:
        raise RuntimeError('Herder environment must be execution-user-owned mode 0600')
    lines = herder.read_text().splitlines()
    if sum(line.startswith('NOTICEPLACE_HEALTH_LAUNCH_TOKEN=') for line in lines) != 1:
        raise RuntimeError('Expected exactly one Herder health credential assignment')
    rendered = '\n'.join('NOTICEPLACE_HEALTH_LAUNCH_TOKEN=' + shlex.quote(tokens[0])
                         if line.startswith('NOTICEPLACE_HEALTH_LAUNCH_TOKEN=') else line
                         for line in lines) + '\n'
    fd, temporary = tempfile.mkstemp(prefix='.health-credential-', dir=herder.parent)
    try:
        os.fchmod(fd, 0o600)
        os.fchown(fd, info.st_uid, info.st_gid)
        with os.fdopen(fd, 'w') as output:
            output.write(rendered)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, herder)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print('Herder protected credential synchronized; restart required')
    for destination in (Path('/etc/gptadmin/agent-jobs.json'),
                        Path('/home/roomhacker/.config/gptadmin/agent-jobs.json')):
        info = destination.stat()
        if stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != 1000:
            raise RuntimeError('Job profiles must be execution-user-owned mode 0600')
        document = json.loads(destination.read_text())
        for name in ('health-diagnosis', 'health-remediation'):
            document['profiles'][name]['health_launch_token'] = tokens[0]
        fd, temporary = tempfile.mkstemp(prefix='.health-credential-', dir=destination.parent)
        try:
            os.fchmod(fd, 0o600)
            os.fchown(fd, info.st_uid, info.st_gid)
            with os.fdopen(fd, 'w') as output:
                json.dump(document, output, ensure_ascii=False, indent=2)
                output.write('\n')
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        print(str(destination) + ': both existing profiles synchronized; mode 0600 preserved')


if __name__ == '__main__':
    main()
