# NoticePlace portable Hermes loader repair — 2026-10-12

Owner: native Codex01a12c47-a796-7d52-b59c-ad551e0a8752.
Scope: existing Notice Agent Plugin continuation; no GSD bootstrap/artifacts created.
Live Notice/Hermes core, project grants, old questions/results/repairs remain untouched.

Official Hermes c57331677d7fc46298a7159cf3a650853f0966c5 portable loader and
Agent Plugins1.0 section7.2.1 require a bare executable or ./plugin-relative command.
Actual v0.1.0 loader reproduced MCP rejection, although its skill still loaded.
Version0.1.1 ships ./bin/ask-human-mcp, which execs only the original
/opt/noticeplace/bin/ask-human-mcp with unchanged argv and protected ASK_HUMAN_ENV_FILE.
Codex/ZCode native MCP, OpenCode and FastAgent command/auth projections remain
unchanged. Native manifests/marketplaces select the same new versioned bundle.
Version0.1.0 file hashes stayed identical; no overwrite/manual skill or token copy.

Meaningful RED via actual official load_agent_plugin: NoticePlace server absent,
command must be a bare executable or begin with ./. GREEN4/4 focused integration,
including installed root with spaces, exact resolved cwd/command, executable asset,
native projections/skill compatibility, syntax and package validator. Executable
release1.521s, 2GiB/two CPU slots/180s deadline, peak RSS63,232KiB. Logs retained
in ignored .tmp/plugin-hermes-loader-{red,green}.log and plugin-hermes-release.log.
Each loader/compatibility check expects<1s/max5s; native consumer/install bounded
separately. No baseline-wide or nightly retry.

Canonical Notice main checkout holds ten foreign staged paths. Before/after guard
compares staged diff bytes/blob entries and those working file bytes. Publish is a
scoped fast-forward from the already published source base; canonical checkout/index
is not reset, switched or overwritten. Only package/marketplace/tests/report paths
are committed; runtime core unchanged.

Supported install is official hermes plugins install of the Git subdirectory,
--ref exact published main SHA --force --enable --yes-deps. No second MCP entry.
Native acceptance must load the installed plugin through Hermes PluginManager,
connect only its NoticePlace server through native MCP discovery, and invoke exactly
one read-only noticeplace_instructions via the native registry. No LLM/native session
creation or old human-request/callback/repair replay.

Native install/consumer outcome will be appended after the published-source install.
