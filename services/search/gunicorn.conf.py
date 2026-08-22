from __future__ import annotations

accesslog = "-"
errorlog = "-"
access_log_format = (
    '{"remote":"%(h)s","method":"%(m)s","path":"%(U)s","query":"%(q)s",'
    '"status":%(s)s,"bytes":%(B)s,"duration_us":%(D)s,"user_agent":"%(a)s"}'
)
