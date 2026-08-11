"""Prompt construction, the model's JSON contract, and server-side citation
validation (ARCHITECTURE.md §5). The model's output is treated as untrusted
input end to end: a citation only survives if it points at a chunk that was
actually retrieved *and* the marker it claims also appears in the prose.
"""

import json
import re
from dataclasses import dataclass

from pydantic import ValidationError

from .models import AnswerPayload, Citation, LLMAnswer, RetrievedSource
from .providers.llm import LLMClient

SYSTEM_PROMPT = """You are a document question-answering assistant. You answer \
strictly from the numbered source excerpts you are given — never from prior \
knowledge.

Rules:
1. Cite inline, per claim, using the source's bracket number — e.g. "The \
deadline is 30 June [2], extended from 15 June [3]." Every factual sentence \
should carry at least one citation. Do not add a citation list at the end.
2. If the excerpts don't contain enough to answer, set "insufficient_context" \
to true, leave "citations" empty, and write one honest sentence in "answer" \
saying so — do not guess or answer from outside knowledge.
3. If the question is ambiguous, answer the single most likely reading and \
state that reading in "assumption" (e.g. "Taking 'the deadline' to mean the \
submission deadline in §4…"). Otherwise leave "assumption" null.

Respond with ONLY a JSON object, no prose outside it, matching exactly:
{"answer": string, "citations": [int, ...], "insufficient_context": bool, "assumption": string|null}

"citations" must list every source number actually cited in "answer", and \
nothing else."""

_MARKER_RE = re.compile(r"\[(\d+)\]")


@dataclass
class GeneratedAnswer:
    payload: AnswerPayload
    prompt_tokens: int
    output_tokens: int


def build_user_prompt(question: str, sources: list[RetrievedSource]) -> str:
    blocks = []
    for s in sources:
        pages = (
            f"p.{s.page_start}" if s.page_start == s.page_end else f"pp.{s.page_start}-{s.page_end}"
        ) if s.page_start else "p.?"
        blocks.append(f"[{s.marker}] ({s.filename}, {pages})\n{s.snippet}")
    context = "\n\n".join(blocks)
    return f"Sources:\n{context}\n\nQuestion: {question}"


def parse_llm_answer(raw_text: str) -> LLMAnswer:
    """The gateway supports response_format=json_object, but the model's
    output is still untrusted — validate the shape, and fall back to
    treating it as prose (extracting [n] markers directly) if it isn't
    parseable JSON at all."""
    try:
        data = json.loads(raw_text)
        return LLMAnswer(**data)
    except (json.JSONDecodeError, TypeError, ValidationError):
        markers = sorted({int(m) for m in _MARKER_RE.findall(raw_text)})
        return LLMAnswer(
            answer=raw_text.strip(),
            citations=markers,
            insufficient_context=False,
            assumption=None,
        )


def validate_citations(
    llm_answer: LLMAnswer, sources: list[RetrievedSource]
) -> list[Citation]:
    """A citation survives only if: (1) it's in range of chunks actually
    retrieved, and (2) the same marker appears in the prose. A model that
    cites [11] out of 8 chunks, or lists a citation the prose never used,
    produces a dropped marker here — not a broken link."""
    by_marker = {s.marker: s for s in sources}
    prose_markers = {int(m) for m in _MARKER_RE.findall(llm_answer.answer)}

    validated: list[Citation] = []
    seen: set[int] = set()
    for marker in llm_answer.citations:
        if marker in seen or marker not in by_marker or marker not in prose_markers:
            continue
        s = by_marker[marker]
        validated.append(
            Citation(
                marker=marker,
                chunk_id=s.chunk_id,
                document_id=s.document_id,
                filename=s.filename,
                page_start=s.page_start,
                page_end=s.page_end,
                char_start=s.char_start,
                char_end=s.char_end,
            )
        )
        seen.add(marker)
    return validated


async def generate_answer(
    llm: LLMClient, question: str, sources: list[RetrievedSource]
) -> GeneratedAnswer:
    completion = await llm.complete(
        system=SYSTEM_PROMPT,
        user=build_user_prompt(question, sources),
        temperature=0.0,
        max_tokens=1000,
        json_mode=True,
    )
    llm_answer = parse_llm_answer(completion.text)

    if llm_answer.insufficient_context:
        # Belt and braces (ARCHITECTURE.md §5): force citations empty
        # regardless of what the model attached, in case it declared
        # insufficiency but still hallucinated a citations array.
        payload = AnswerPayload(
            text=llm_answer.answer
            or "I don't have enough information in your documents to answer this.",
            citations=[],
            abstained=True,
            abstain_reason="model_declined",
            assumption=llm_answer.assumption,
        )
    else:
        payload = AnswerPayload(
            text=llm_answer.answer,
            citations=validate_citations(llm_answer, sources),
            abstained=False,
            abstain_reason=None,
            assumption=llm_answer.assumption,
        )

    return GeneratedAnswer(
        payload=payload,
        prompt_tokens=completion.prompt_tokens,
        output_tokens=completion.output_tokens,
    )
