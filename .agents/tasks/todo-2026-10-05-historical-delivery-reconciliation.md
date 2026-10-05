# Reconcile historical uncertain NoticePlace deliveries

Status: deferred from the phone-call recovery because external Telegram send
outcomes cannot be rewritten safely without an explicit reconciliation rule.

After the health-query indexes shipped in `97d6d3a`, authenticated `/health`
responds in about 0.5 seconds instead of timing out. It correctly remains
degraded because the database contains 1,372 historical `uncertain` deliveries
and eight stale `sending` deliveries. Most uncertain rows belong to already
resolved `fleet-health` incidents; active rows also exist for retired
`health-monitor`, fail2ban, Hermes, and other projects.

Do not retry these sends: their external outcome is unknown. The smallest next
action is to add an audited operator reconciliation command that can mark a
specific delivery `sent`, `failed`, or `superseded` from independent Telegram
receipt evidence, then reconcile resolved incidents first and review each
remaining active project separately.
