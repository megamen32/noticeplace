"""Public, token-free landing representation for the NoticePlace hostname."""

from __future__ import annotations


LANDING_PAGE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>NoticePlace</title>
  <style>
    :root { --ink:#e9edf7; --muted:#aeb9cf; --panel:rgba(19,31,55,.78); --line:#31466f; --accent:#8a7dff; --good:#51d3a2; }
    * { box-sizing:border-box; }
    body { margin:0; min-height:100vh; color:var(--ink); font:16px/1.5 Inter,ui-sans-serif,system-ui,sans-serif; background:radial-gradient(900px 480px at 92% -10%,#3d347b 0%,transparent 65%),radial-gradient(650px 420px at -10% 100%,#113e56 0%,transparent 65%),#091222; }
    main { width:min(1080px,calc(100% - 40px)); margin:auto; padding:76px 0 48px; }
    .eyebrow { color:var(--good); font-size:.78rem; font-weight:700; letter-spacing:.13em; text-transform:uppercase; }
    h1 { max-width:760px; margin:14px 0; font-size:clamp(2.8rem,8vw,6rem); line-height:.96; letter-spacing:-.065em; }
    .lead { max-width:650px; margin:0; color:var(--muted); font-size:clamp(1.05rem,2vw,1.25rem); }
    .grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin:54px 0; }
    .card { min-height:168px; padding:24px; border:1px solid var(--line); border-radius:20px; background:var(--panel); box-shadow:0 14px 48px rgba(0,0,0,.15); }
    .card b { display:block; margin:12px 0 6px; font-size:1.06rem; }
    .card p { margin:0; color:var(--muted); font-size:.93rem; }
    .mark { display:grid; width:36px; height:36px; place-items:center; border-radius:11px; background:#28245b; color:#b9b2ff; font-weight:800; }
    .api { display:flex; align-items:center; justify-content:space-between; gap:20px; padding:21px 24px; border:1px solid var(--line); border-radius:18px; background:#0b172b; }
    code { color:#c4bcff; font:600 .9rem ui-monospace,SFMono-Regular,Consolas,monospace; overflow-wrap:anywhere; }
    .note { margin:26px 0 0; color:var(--muted); font-size:.9rem; }
    a { color:#bdb6ff; }
    @media (max-width:720px) { main { padding-top:52px; } .grid { grid-template-columns:1fr; margin:38px 0; } .api { align-items:flex-start; flex-direction:column; } }
  </style>
</head>
<body>
  <main>
    <div class="eyebrow">Notification infrastructure</div>
    <h1>NoticePlace</h1>
    <p class="lead">One durable place for operational events, acknowledgements, and escalation—without putting delivery credentials into every project.</p>
    <section class="grid" aria-label="Capabilities">
      <article class="card"><div class="mark">01</div><b>Scoped events</b><p>Each producer is limited to its own project and permitted severity.</p></article>
      <article class="card"><div class="mark">02</div><b>Clear response</b><p>Clients can optionally wait for an operator acknowledgement or resolution.</p></article>
      <article class="card"><div class="mark">03</div><b>Controlled escalation</b><p>Routing and call policy remain centralized rather than copied into services.</p></article>
    </section>
    <section class="api" aria-label="Producer API">
      <div><div class="eyebrow">Producer endpoint</div><code>POST /v1/events</code></div>
      <a href="/admin/">Operator sign in</a>
    </section>
    <p class="note">Operational API endpoints require project-scoped credentials. The health endpoint is intentionally not public.</p>
  </main>
</body>
</html>""".encode("utf-8")
