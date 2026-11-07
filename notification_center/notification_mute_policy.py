"""Stable source identity for legacy Fail2ban keys, including IPv6 addresses."""
import ipaddress


def fail2ban_family(key, project=None):
    parts = str(key).split(':', 3)
    if len(parts) != 4 or parts[0] != 'fail2ban' or not parts[1].startswith('fail2ban.') or not parts[2]:
        return None
    if project is not None and parts[1] != project:
        return None
    try:
        ipaddress.ip_address(parts[3])
    except ValueError:
        return None
    return ':'.join(parts[:3])+':'


def legacy_fail2ban_mutes(connection):
    """Preview only exact-IP mutes with an unambiguous project/jail identity."""
    rows = connection.execute("SELECT project,recipient,dedup_key,muted_at,muted_by,revision FROM notification_mutes WHERE unmuted_at IS NULL AND match_mode='exact' AND project LIKE 'fail2ban.%'").fetchall()
    result = []
    for row in rows:
        family = fail2ban_family(row['dedup_key'], row['project'])
        if not family:
            continue
        existing = connection.execute('SELECT dedup_key FROM notification_mutes WHERE project=? AND recipient=? AND dedup_key=?',
                                      (row['project'],row['recipient'],family)).fetchone()
        if existing is None:
            result.append({**dict(row),'family_key':family})
    return result


def migrate_legacy_fail2ban_mutes(connection, expected):
    """Caller owns transaction; CAS preserves later unmute and concurrent policy changes."""
    actual = legacy_fail2ban_mutes(connection)
    if sorted(actual,key=lambda x:(x['project'],x['recipient'],x['dedup_key'])) != sorted(expected,key=lambda x:(x['project'],x['recipient'],x['dedup_key'])):
        raise ValueError('notification mute preview changed; migration not applied')
    count = 0
    for row in actual:
        exists = connection.execute('SELECT 1 FROM notification_mutes WHERE project=? AND recipient=? AND dedup_key=?',
                                    (row['project'],row['recipient'],row['family_key'])).fetchone()
        if exists is None:
            connection.execute("INSERT INTO notification_mutes(project,recipient,dedup_key,muted_at,muted_by,match_mode) VALUES (?,?,?,?,?,'prefix')",
                               (row['project'],row['recipient'],row['family_key'],row['muted_at'],'migrated:'+str(row['muted_by'])[:100]))
            count += 1
    return count
