# UserIO original message and Markdown delivery — 2026-10-06

Status: completed; reviewed source published, managed runtime installed, native recipient short/card and full Markdown acceptance passed.

User request: show the original incoming text, and immediately deliver a complete Markdown file when it is too long for the card.

- Exact raw original_text is independent of AI summary/reply drafts. Leading/trailing whitespace, Unicode and a tail after 8000 characters are preserved.
- Short original appears first in the card; long original has a preview and exact UTF-8 .md document in the same destination/topic, replying to the card.
- Stable request IDs include original equality; POST/GET report strict telegram_document_required and durable card/document state/IDs. Document file_id is retained for recipient-path verification and reconciliation, with no bot token exposed.
- A card alone cannot confirm mandatory-file delivery. Sending/uncertain/missing receipt stays unconfirmed; known partial delivery is durably quarantined, not replayed or claimed sent, and later events progress.
- The bounded original payload is 100000 UTF-8 bytes. Larger source text stays intact in UserIO and receives a clear failure card with the public product link; it is not silently truncated and does not block later claims.
- Exact Send 1/2 draft IDs, authenticated actor, project/recipient route 5764, tokens and unrelated health/source/card/job functions are preserved.
- Tests: producer40, Notice20; independent gpt-6-sol final review PASS. Worker models gpt-5.6-sol; existing project test cgroup/shared heavy lock used.
- Root acceptance plan: one benign short and one long controlled priority Secretary→Nikita message, receiver body verification, same-topic Notice card/file receipts, full downloaded MD SHA-256 equality. No root reply-button clicks, external mail/Matrix sends or calls.
- One managed Notice upgrade coordinated with owner01a1106f and physical test owner01a10bc5. Attachment canaries begin after the separate physical test terminal receipt; no live actions from implementation workers.
- Final evidence belongs to /home/roomhacker/.codex/visualizations/2026/10/06/01a110c6-203c-7db3-8b86-fce6c3869d35/userio-original-message-md-final.json.

## Final native acceptance

- Managed Notice runtime df4befeb/main173733/admin173743, producer code eb078d0/PID207027. One coordinated upgrade; other owner phone test finished before Helper acceptance. No root phone action or further Notice restart.
- Secretary8810909089 sender2319/2320 → Nikita540308572 receiver1981531/1981532; both exact bodies independently read through Helper. Active account restored11/Nikita.
- Short event39648 eligible1/reconciled0 completed notify → card5860 in topic5764, full295 UTF-16 original first; no document needed. https://t.me/c/4322359393/5764/5860
- Long event39649 eligible1/reconciled0 completed notify → card5861 + replied document5862 in exact topic5764. https://t.me/c/4322359393/5764/5862
- Recipient Helper downloaded metadata: human-request-userio-39649.md, text/markdown,7053 bytes, SHA256 0e777c1f85b20a7be0213ddf50c1cef3a29691150458921342b13f2aedb916ea. Bounded official getFile/download of this own benign file matched all7053 original UTF-8 bytes, including final marker. No token/body secret printed.
- Both claims completed on first attempt with confirmed receipts; no partial/quarantine in these healthy canaries. No root reply-button click or external email/Matrix send.
- Final evidence: current thread userio-original-message-md-final.json and userio-original-long-received.md; executable safe probe retained alongside proof.

## Latest requested historical and combined-file acceptance

- Peer original-backfill source a7cfef7 reviewed by root gpt-6-sol; old-thread fail-closed regression passed before document reservation. Full Notice suite411 passed including new phone owner scope; one managed release daa39d2 live main955716/admin955719, active/NRestarts0.
- Existing cards5842/event39461,5845/event39472,5849/event39515 now contain complete original7/2/330 characters on SAME message IDs; immutable actors/choices/expiry/recipient preserved. Root independently read all three asNikita.5849 resolved Send1 remains visible, no callback/reply repeated.
- User clarified full MD combines ORIGINAL+AI analysis. Peer modeNotify/nochoices benign proof card5899/document5900 exacttopic5764 contains full4000-character original and full AIcard text. Recipient Helper download7955 bytes SHA256 cb32f77313df8f5364a33b570c740374902e3e57a7c08d5f2eb110d3728ef96a equals peer independent Bot download and expected composition. Root independently verified recipient MIME/name/size/hash and exact native thread link https://t.me/c/4322359393/5764/5900.
- Prior raw7053-byte proof5862 remains a historical first-phase receipt; new format is combined Markdown. No additional root DM/AI/call or reply-button action during historical repair.
- Explicit user same-child resume used followup_task for existing /root/matrix_owner_trace; native01a111a2-3201-7883-83fb-5a976e37bc0d confirmed through agent_info and child session_meta. No replacement/session was created. Matrix ownership trace and subsequent readonly phone-writer trace completed; all dirty external work preserved.
