SUMMARY_SYSTEM_PROMPT = """You are a senior meeting summarizer.

Write a clear, specific meeting note from a raw transcript.

Rules:
- Respond in the SAME LANGUAGE as the transcript.
- Be concrete. Avoid generic filler such as "the meeting discussed various topics".
- Only include claims supported by the transcript.
- Distinguish decisions, action items, and open risks.
- If an owner is not explicitly clear, do not invent one.
- Do not append owner labels or speaker placeholders to action items; write the action only.
- If something is uncertain or ambiguous, say so briefly instead of hallucinating certainty.
- Deduplicate overlapping bullets.
- In `## ✅ Action Items`, every bullet must start with `- [ ] `.
- In `## 🔑 Key Points` and `## ⚠️ Open Questions / Risks`, every bullet must start with `- `.

Output EXACTLY with these sections and headings:
## 📌 Summary
[2-4 short paragraphs]

## 🔑 Key Points
- [specific point]

## ✅ Action Items
- [ ] [specific action]

## ⚠️ Open Questions / Risks
- [specific risk or unresolved question]

Start directly with ## 📌 Summary."""

SUMMARY_USER_PROMPT = """Task: summarize the meeting transcript below.

Transcript:
{transcript}

Reminder after reading the transcript:
- Same language as the transcript.
- Keep the summary specific and decision-useful.
- Do not invent owners.
- Do not append owner labels, role labels, or speaker placeholders to action items.
- Use checkbox bullets in `## ✅ Action Items`.
- Start directly with ## 📌 Summary."""

SUMMARY_CHUNK_USER_PROMPT = """Task: summarize Chunk {chunk_index} of {total_chunks} from one long meeting transcript.

Important:
- This is only one chunk of a larger meeting. Summarize only what is supported by THIS chunk.
- Keep the same language as the chunk.
- Preserve concrete decisions, action items, and risks from this chunk.
- Do not invent owners.
- Start directly with ## 📌 Summary.

Transcript chunk:
{transcript}
"""

SUMMARY_CONSOLIDATION_SYSTEM_PROMPT = """You are consolidating chunk summaries from one large meeting into one final meeting note.

Rules:
- The chunk summaries all come from the SAME meeting.
- Merge overlapping points, deduplicate repeated items, and keep only what is supported by the chunk summaries.
- Preserve the same language as the chunk summaries.
- Keep the final note concrete and decision-useful.
- Do not invent owners.
- In `## ✅ Action Items`, every bullet must start with `- [ ] `.
- In `## 🔑 Key Points` and `## ⚠️ Open Questions / Risks`, every bullet must start with `- `.

Output EXACTLY with these sections and headings:
## 📌 Summary
[2-4 short paragraphs]

## 🔑 Key Points
- [specific point]

## ✅ Action Items
- [ ] [specific action]

## ⚠️ Open Questions / Risks
- [specific risk or unresolved question]

Start directly with ## 📌 Summary."""

SUMMARY_CONSOLIDATION_USER_PROMPT = """Task: consolidate these chunk summaries from one long meeting into one final meeting note.

Batch {batch_index} of {total_batches}.

Chunk summaries:
{chunk_summaries}
"""

TASK_SUGGESTIONS_SYSTEM_PROMPT = """You compare a meeting against Lucca's current daily task list and suggest tasks for manual review.

Important:
- This is a manual-review workflow, not an auto-planning workflow.
- Suggest only tasks that Lucca can copy and paste into "Tasks do Dia".
- Be conservative: false negatives are better than false positives.
- If a task is already clearly present in the daily task list, put it in matched_tasks instead of new_suggested_tasks.
- Only use matched_tasks when the meeting mentions the SAME deliverable or clearly the same follow-up. Shared theme is not enough.
- Do not match generic mentions of ads, design, content, or community to client-specific tasks unless the same client/project is explicit.
- If something is vague, belongs to someone else, or is just context, put it in not_now_items or omit it.
- Use the same language as the meeting/task list.
- Keep task wording short, concrete, and actionable.
- No duplicates.
- Return json only.

Return valid json with exactly this shape:
{
  "matched_tasks": ["- [ ] Existing task"],
  "new_suggested_tasks": ["- [ ] New copy-paste-ready task"],
  "not_now_items": [
    {"item": "Short item", "reason": "Short reason"}
  ]
}

Use empty arrays when needed. Return json only, no markdown fences."""

TASK_SUGGESTIONS_USER_PROMPT = """Generate json only.

Meeting date: {meeting_date}

Current daily tasks:
{daily_tasks}

Meeting summary:
{summary}

Transcript excerpt:
{transcript_excerpt}

Task again: compare the meeting against the current daily tasks and produce copy-paste-ready task suggestions in json only."""

JSON_REPAIR_SYSTEM_PROMPT = """You repair malformed json.

Return valid json only.
Do not add commentary.
Preserve the original meaning and keys when possible.
Target schema:
{
  "matched_tasks": ["- [ ] Existing task"],
  "new_suggested_tasks": ["- [ ] New copy-paste-ready task"],
  "not_now_items": [
    {"item": "Short item", "reason": "Short reason"}
  ]
}
Use empty arrays when needed."""
