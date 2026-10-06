# UserIO original message and Markdown delivery — 2026-10-06

Status: reviewed source ready; managed Notice release and native recipient acceptance follow in the coordinated window.

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
