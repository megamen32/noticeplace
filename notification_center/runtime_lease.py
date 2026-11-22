"""Elect a short-lived task owner in the shared transaction store."""


def claim(connection, name, holder, now, ttl_seconds):
    connection.execute(
        "INSERT INTO runtime_leases(name,holder,expires_at) VALUES (?,?,?) "
        "ON CONFLICT(name) DO UPDATE SET holder=excluded.holder,expires_at=excluded.expires_at "
        "WHERE runtime_leases.holder=excluded.holder OR runtime_leases.expires_at<=?",
        (name, holder, now + max(5, min(120, ttl_seconds)), now),
    )
    row = connection.execute("SELECT holder FROM runtime_leases WHERE name=?", (name,)).fetchone()
    return row is not None and row["holder"] == holder
