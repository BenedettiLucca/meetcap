from .config import (
    SUMMARY_MAX_TOKENS,
    SUMMARY_TEMPERATURE,
    SUMMARY_SINGLE_PASS_MAX_CHARS,
    SUMMARY_CHUNK_MAX_CHARS,
    SUMMARY_MERGE_MAX_CHARS,
)
from .prompts import (
    SUMMARY_SYSTEM_PROMPT,
    SUMMARY_USER_PROMPT,
    SUMMARY_CHUNK_USER_PROMPT,
    SUMMARY_CONSOLIDATION_SYSTEM_PROMPT,
    SUMMARY_CONSOLIDATION_USER_PROMPT,
)
from .llm_client import call_openrouter
from .transcript_parser import split_transcript_into_chunks

def summarize_transcript_chunk(transcript_text: str, chunk_index: int, total_chunks: int) -> str:
    """Summarize one transcript chunk using the normal meeting-note schema."""
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": SUMMARY_CHUNK_USER_PROMPT.format(
                chunk_index=chunk_index,
                total_chunks=total_chunks,
                transcript=transcript_text,
            ),
        },
    ]
    return call_openrouter(
        messages=messages,
        max_tokens=SUMMARY_MAX_TOKENS,
        temperature=SUMMARY_TEMPERATURE,
        reasoning_effort="none",
    )

def group_texts_by_char_budget(texts: list[str], max_chars: int) -> list[list[str]]:
    """Group texts into batches whose formatted size stays under the budget."""
    batches: list[list[str]] = []
    current_batch: list[str] = []
    current_len = 0

    for text in texts:
        block_len = len(text) + 24
        projected = block_len if not current_batch else current_len + 2 + block_len
        if current_batch and projected > max_chars:
            batches.append(current_batch)
            current_batch = [text]
            current_len = block_len
        else:
            current_batch.append(text)
            current_len = projected if current_batch[:-1] else block_len

    if current_batch:
        batches.append(current_batch)

    return batches or [texts]

def format_chunk_summaries(chunk_summaries: list[str]) -> str:
    """Render chunk summaries in a readable merge format."""
    return "\n\n".join(
        f"### Chunk Summary {index}\n{summary}"
        for index, summary in enumerate(chunk_summaries, start=1)
    )

def merge_summary_batch(chunk_summaries: list[str], batch_index: int, total_batches: int) -> str:
    """Merge one batch of chunk summaries into a consolidated summary."""
    messages = [
        {"role": "system", "content": SUMMARY_CONSOLIDATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": SUMMARY_CONSOLIDATION_USER_PROMPT.format(
                batch_index=batch_index,
                total_batches=total_batches,
                chunk_summaries=format_chunk_summaries(chunk_summaries),
            ),
        },
    ]
    return call_openrouter(
        messages=messages,
        max_tokens=SUMMARY_MAX_TOKENS,
        temperature=SUMMARY_TEMPERATURE,
        reasoning_effort="none",
    )

def reduce_chunk_summaries(chunk_summaries: list[str], merge_max_chars: int) -> str:
    """Hierarchically merge chunk summaries until a single final summary remains."""
    current_level = chunk_summaries[:]
    while len(current_level) > 1:
        batches = group_texts_by_char_budget(current_level, merge_max_chars)
        current_level = [
            merge_summary_batch(batch, index, len(batches))
            for index, batch in enumerate(batches, start=1)
        ]
    return current_level[0]

def generate_summary(
    transcript_text: str,
    *,
    single_pass_max_chars: int = SUMMARY_SINGLE_PASS_MAX_CHARS,
    chunk_max_chars: int = SUMMARY_CHUNK_MAX_CHARS,
    merge_max_chars: int = SUMMARY_MERGE_MAX_CHARS,
) -> str:
    """Generate the meeting summary block, chunking long meetings before consolidation."""
    try:
        if len(transcript_text) <= single_pass_max_chars:
            messages = [
                {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                {"role": "user", "content": SUMMARY_USER_PROMPT.format(transcript=transcript_text)},
            ]
            return call_openrouter(
                messages=messages,
                max_tokens=SUMMARY_MAX_TOKENS,
                temperature=SUMMARY_TEMPERATURE,
                reasoning_effort="none",
            )

        chunks = split_transcript_into_chunks(transcript_text, max_chars=chunk_max_chars)
        chunk_summaries = [
            summarize_transcript_chunk(chunk, index, len(chunks))
            for index, chunk in enumerate(chunks, start=1)
        ]
        return reduce_chunk_summaries(chunk_summaries, merge_max_chars)
    except Exception as exc:
        return f"> [!warning] Summary generation failed: {exc}"
