# Voice confirmation of a phone incident

See the [communications architecture](communications-architecture.md) for
ownership and state semantics. AgentCall is an internal UserIO voice component;
this document describes the existing Notice Place incident-acknowledgement
integration, not a public AgentCall API or an AskHuman answer protocol.

When the AgentCall gateway recognizes an explicit remote reply such as
«Да, я услышал», Notice Place acknowledges that exact incident and cancels its
pending repeats. The conversation continues through the configured voice LLM.
Acknowledgement records that the person received the alert; it does not claim
that a health problem was repaired or resolved.

The delivery worker persists the returned `call_id`/`receipt_id` from the
existing transport adapter. One local subscriber reads the existing
`gatewayd.sock` transcript events and accepts
only remote confirmations whose call ID matches that durable phone receipt.
Questions, negative confirmations, agent speech, and unknown call IDs cannot
acknowledge an incident. Repeated confirmation is idempotent, and the result
survives a Notice Place restart.

The gateway event socket must be `root:notification-center`, mode `0660`, just
like its control socket. No broad API credential is passed to the gateway.
The subscriber adds one thread inside the existing main-service resource
budget; its input frame is capped at 64 KiB and reconnect delay is three seconds.

Before dialing, the worker durably reserves that delivery generation. A
transport timeout or reset with an unknown outcome marks it `uncertain` rather
than automatically dialing again. A known pre-call rejection remains retryable.
Existing alternate delivery methods retain their policy.

Verification must include a real remote phrase, its matching phone receipt,
the `incident_acknowledged` audit record, cancellation of pending repeats, and
an audible reply. Unit tests alone do not prove the cellular audio path.
