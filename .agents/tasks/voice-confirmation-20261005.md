# Voice replies and acknowledgement: deployed proof

Status: deployed and verified on 2026-10-05, server-100.

The user reported silent voice replies and repeated calls after saying that
the alert was heard. The infrastructure gateway was using shared server-44
Gemma instead of the expected MiniMax, and its quiet-speech turn detector
truncated continuous low-volume phrases. The infrastructure owner repaired
those paths and deployed commit `b78141d`.

Notice Place did not persist phone receipts or consume remote confirmations.
The delivery worker now reserves the call generation, preserves its receipt,
and avoids redial after an unknown transport outcome. One local subscriber
binds an explicit remote confirmation to that exact receipt and performs the
existing durable incident ACK. It cancels pending escalation without claiming
that the health problem was repaired. Deployed source is `6f8e0e0`.

Validation: 73 focused phone/delivery tests and 10 consumer-policy tests pass.
Questions, negative confirmations, unknown calls and agent speech cannot ACK;
repeated confirmation is idempotent and survives reopening the database.

Live proof: call `23ecbed8-2ce5-48cf-bb4a-29f80342f5fb` on incident
`inc_8dec6d7e8216451cbee3fcda9eca168c` recognized
«Хорошо, я тебя услышал.» at 14:41:49 MSK. The matching
`incident_acknowledged` audit was persisted 0.037 seconds later with actor
`phone:<call_id>`. Its pending Matrix escalation was cancelled. MiniMax
responded «Принято. Буду держать вас в курсе, если ситуация изменится.» after
2.31 seconds, and UserIO preserved both speakers and clean call end.
Independent health recovery later resolved the incident; the voice ACK did
not bypass the health-resolution gate.

A separate controlled test call recognized only «Да.», which intentionally
does not ACK. Its synthetic test incident was resolved by the operator after
call end to suppress its later test repeat. It is not used as voice-ACK proof.

Infrastructure budget: bridge 1.5/2 GiB RAM, 512 MiB swap, two CPU cores,
128 tasks. The event socket is root:notification-center mode 0660. Both
Notice Place services, the gateway and the UserIO ingress are active. The
task phone lease was released; no active Asterisk call remained at cleanup.
