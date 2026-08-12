# PIAB — potential items for future review

Tracking ideas and accepted v1 trade-offs that may deserve a second look later.

---

## Sync confidence: preview vs full-length correlation

**Status:** Accepted for v1 (with guardrail below)

On a 5-minute Fast Preview, cross-correlation for video/audio sync may differ from a
full-length interview. We accept that the user’s A/B choice on preview may not match what
full-length analysis would have shown.

**v1 guardrail (not deferred):** If preview sync confidence is low enough that a
full-length run would trigger the A/B gate, Fast Preview **must** still render both
preview 1-minute tests (start-aligned and forced-offset) and show **F2a** — same as
today. Do not ignore low confidence on preview.

**Future review:** After field use, check whether preview A/B choices ever disagree with
what full-length sync reports would suggest, and whether we should re-check confidence
after full-length sync (without re-prompting unless mismatch is severe).

---
