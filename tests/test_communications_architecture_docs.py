from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "docs" / "communications-architecture.md"
REFERENCES = {
    "README.md": "docs/communications-architecture.md",
    "README.ru.md": "docs/communications-architecture.md",
    "README.zh.md": "docs/communications-architecture.md",
    "docs/features.md": "communications-architecture.md",
    "docs/producer.md": "communications-architecture.md",
    "docs/human-request.md": "communications-architecture.md",
    "docs/personal-question-calls.md": "communications-architecture.md",
    "docs/secretary-phone.md": "communications-architecture.md",
    "docs/notification-center-mvp.md": "communications-architecture.md",
    "docs/admin.md": "communications-architecture.md",
    "docs/voice-confirmation.md": "communications-architecture.md",
    "docs/producer-sdk.md": "communications-architecture.md",
    "docs/incident-call-chain.md": "communications-architecture.md",
    "docs/gptadmin-phone-adapter.md": "communications-architecture.md",
    "docs/mandatory-e2e-supertest.md": "communications-architecture.md",
    "docs/mcp.md": "communications-architecture.md",
    "docs/mcp.ru.md": "communications-architecture.md",
    "docs/mcp.zh.md": "communications-architecture.md",
    "deploy/README.md": "../docs/communications-architecture.md",
}


def test_one_canonical_boundary_document_names_component_ownership():
    text = CANONICAL.read_text().casefold()
    for phrase in (
        "notice place provides the public api and mcp",
        "userio is the communications gateway",
        "agentcall is an internal userio",
        "matrix and telemost are separate channels",
        "not assumed or required to use the cellular asterisk chain",
        "agent herder alone resumes",
        "durably stored",
        "confirmed human answer",
        "legacy direct",
    ):
        assert phrase in text


def test_affected_docs_link_to_the_canonical_boundary():
    for relative_path, link in REFERENCES.items():
        text = (ROOT / relative_path).read_text()
        assert link in text, relative_path
