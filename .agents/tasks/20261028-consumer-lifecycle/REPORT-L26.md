# L26 source continuation identity and bounded prelaunch receipt

Published source: `51de9b55ce87a92df189de2fae549d9559d71929`, authoritative origin/main verified. Four owned helper/adapter/test paths only; foreign WIP preserved.

Actual server delivery target carries validated source-check continuation authority. Initial operation IDs remain unchanged. Continuation operation identity binds the finished attempt and native session; exact retries and rotated polling nonces retain the same operation. A later finished attempt receives another operation. New native attempt IDs remain delivery-bound.

Explicit completed source_blocked / agent-herder-owner-disarm / herder_owner_state_unknown receipts preserve admission=not_attempted and operation_id through the bounded adapter. Missing admission, blank fields, timeout, accepted/UNKNOWN or contradictory native acceptance never become negative proof. No secret-bearing raw payload is retained.

RED: actual adapter→run_profile→project-launch wire used the initial operation for continuation. GREEN: 83 focused tests passed in 45.005 seconds; root independently accepted final delta.

Runtime not yet updated. Existing isolated incident `inc_c1092c3c91514485a0617072e87a7727` has no native session. First Hub job `55a4eccf5492b0fa4d830c31d0d9aba3` stopped before project-launch; its historical receipt lacks the newly explicit admission field and is not automatically inferred safe. Reconciliation requires supported authenticated expected-operation NOT_FOUND and current runtime/STOP proof, then the L28 domain seam. No replay or second incident.

## Installed proof

Combined6a9f78fe installed all3; real independent503 continuation produced `b96c74a15313a9179b493bd4f8e86cdb` (initiale0fff…different), exactsame native `ses_e7c849ab9ffeA5lL9rAli7jl1R`, created=false. Actual newattempt566b412…repaired andfinished; independent200resolved. No duplicate session or extra sourceoperation. See RECEIPT-L17.json.
