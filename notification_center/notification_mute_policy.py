"""Stable source identity for legacy Fail2ban keys, including IPv6 addresses."""
import ipaddress
import time


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
    retire_migrated_exact_mutes(connection, actual)
    return count


def retire_migrated_exact_mutes(connection, preview):
    """Finish a migration using its saved preview, never guessed IP ownership.

    Caller owns the transaction. Old deployments retained exact rows; only an
    unchanged migrated family and exact snapshots may be retired. A subsequent
    operator write (even at the same timestamp) changes revision and survives.
    """
    families = {}
    for row in preview:
        family = fail2ban_family(row['dedup_key'], row['project'])
        if not family or family != row['family_key']:
            raise ValueError('invalid notification mute migration preview')
        families.setdefault((row['project'], row['recipient'], family), []).append(row)
    count = 0
    for scope, rows in families.items():
        rule = connection.execute(
            'SELECT * FROM notification_mutes WHERE project=? AND recipient=? AND dedup_key=?', scope).fetchone()
        if (rule is None or rule['match_mode'] != 'prefix' or rule['unmuted_at'] is not None
                or rule['revision'] != 0 or not any(
                    rule['muted_at'] == row['muted_at']
                    and rule['muted_by'] == 'migrated:'+str(row['muted_by'])[:100] for row in rows)):
            continue
        for row in rows:
            changed = connection.execute(
                "UPDATE notification_mutes SET unmuted_at=?,unmuted_by='migration:family',revision=revision+1 "
                "WHERE project=? AND recipient=? AND dedup_key=? AND match_mode='exact' AND unmuted_at IS NULL "
                "AND muted_at=? AND muted_by=? AND revision=?",
                (time.time(), row['project'], row['recipient'], row['dedup_key'],
                 row['muted_at'], row['muted_by'], row['revision']))
            count += changed.rowcount
    return count
