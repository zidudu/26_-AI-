---
name: deliver-outlook-reports
description: Windows Classic Outlook COM으로 보고서 메일 초안을 만들거나 사용자가 승인한 발송 자동화를 구현할 때 사용한다. 발송 의도 기록, 계정·수신자 확인, 첨부 검증, 전송 불확실성과 재시도 처리를 다룬다. 사용자 승인 없는 실제 전송에는 사용하지 않는다.
---

# Outlook 보고서 발송 관리

## Workflow
1. Establish whether the task authorizes a draft or actual sending. Reuse explicit authorization already given; otherwise produce a reviewable draft and do not call Send. Read [delivery state contract](references/contracts.md).
2. Verify Windows Classic Outlook COM availability, an active profile and the selected sender account. Do not assume new Outlook supports the same COM interface. Resolve every recipient and validate To/CC plus attachment hashes and readability.
3. Build the exact subject/body/attachments and persist the approved payload hash and unique delivery key. Keep summaries distinct from full attachments according to the user's request.
4. Write a durable send intent before the external send operation. Execute COM in its owning initialized thread; track owned items/resources. A crash or ambiguous exception after intent becomes unknown, not retryable by default.
5. Treat Send returning as submitted, not delivered. Reconcile using an application-owned run identifier and observable Outlook state; never infer completion from a log line alone.
6. On unknown, stop automatic resend. Inspect evidence or ask whether to send again if uncertainty cannot be resolved. Only retry when non-submission is established or a new explicit authorization covers the risk.

Deliver the draft or authorized send result, exact recipients/attachments and honest state. Test with a fake mail adapter for crash boundaries. Live COM availability and a synthetic state-machine test do not prove real mailbox delivery.
