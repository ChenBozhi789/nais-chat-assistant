"""Local experimental agent: Foundry chat model + custom file-reading tools.

Run from the repo root:
    python -m mcp_tools.local_agent.agent "Do I need to book a session?"
    python -m mcp_tools.local_agent.agent            # interactive
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from openai import OpenAI

from backend.config import settings
from mcp_tools.local_agent.docs_tools import TOOL_SCHEMAS, dispatch

DEFAULT_MAX_ROUNDS = 8
INSTRUCTIONS_PATH = Path(__file__).with_name("instructions.md")
SOURCE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.md")


def system_prompt() -> str:
    """Agent instructions from ``instructions.md`` (tool use is mandatory)."""
    text = INSTRUCTIONS_PATH.read_text(encoding="utf-8")
    return text.replace("{contact_info}", settings.nais_contact_info)


def local_model() -> str:
    """Use ``LOCAL_AGENT_MODEL``, or fall back to the chat deployment."""
    return (
        os.getenv("LOCAL_AGENT_MODEL", "").strip() or settings.foundry_chat_deployment
    )


def make_client() -> OpenAI:
    missing = [
        k
        for k, v in {
            "FOUNDRY_OPENAI_BASE_URL": settings.foundry_openai_base_url,
            "FOUNDRY_API_KEY": settings.foundry_api_key,
            "LOCAL_AGENT_MODEL or FOUNDRY_CHAT_DEPLOYMENT": local_model(),
        }.items()
        if not v
    ]
    if missing:
        raise SystemExit(f"Missing .env settings: {', '.join(missing)}")
    return OpenAI(
        base_url=settings.foundry_openai_base_url,
        api_key=settings.foundry_api_key,
        timeout=settings.llm_timeout_seconds * 3,
        max_retries=1,
    )


def _tool_choice(searches: int, candidates: list[str], files_read: list[str]) -> Any:
    """Require discovery, a synonym retry, then candidate reading before a reply."""
    name = ""
    if searches == 0 or (searches == 1 and not candidates):
        name = "search_documents"
    elif any(candidate not in files_read for candidate in candidates):
        name = "read_document"
    if name:
        return {"type": "function", "function": {"name": name}}
    return "auto"


def _valid_sources(answer: str, files_read: list[str]) -> bool:
    """Check citation provenance; factual support still needs human review."""
    source_section = re.search(r"(?im)^Sources:[ \t]*", answer)
    if not source_section:
        return False
    cited = set(SOURCE_NAME_RE.findall(answer[source_section.end() :]))
    return bool(cited) and cited <= set(files_read)


def run_agent(
    question: str,
    client: Any | None = None,
    model: str | None = None,
    max_rounds: int | None = None,
    verbose: bool = True,
) -> tuple[str, list[str]]:
    """Tool-calling loop. Returns (answer, filenames read)."""
    client = client or make_client()
    model = model or local_model()
    rounds = max_rounds or int(os.getenv("LOCAL_AGENT_MAX_ROUNDS", DEFAULT_MAX_ROUNDS))
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": question},
    ]
    files_read: list[str] = []
    searches = 0
    candidates: list[str] = []
    source_revisions = 0

    for round_no in range(1, rounds + 1):
        choice = _tool_choice(searches, candidates, files_read)
        next_candidate = next(
            (name for name in candidates if name not in files_read), None
        )
        if next_candidate and choice != "auto":
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"Read {next_candidate} in full before answering. "
                        "It is a relevant search candidate; "
                        "do not infer facts from the snippet."
                    ),
                }
            )
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOL_SCHEMAS,
            tool_choice=choice,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            answer = (msg.content or "").strip()
            if choice != "auto":
                messages.append({"role": "assistant", "content": answer})
                messages.append(
                    {
                        "role": "user",
                        "content": "Call the required document tool first.",
                    }
                )
                continue
            if files_read and not _valid_sources(answer, files_read):
                if source_revisions >= 2:
                    return "(Stopped: could not verify answer sources.)", files_read
                source_revisions += 1
                messages.append({"role": "assistant", "content": answer})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Revise the answer. End with one Sources: line citing only "
                            f"the files you read: {', '.join(files_read)}. "
                            "Ensure each cited file supports the associated fact."
                        ),
                    }
                )
                continue
            return answer, files_read
        messages.append(
            {
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            }
        )
        for tc in msg.tool_calls:
            result = dispatch(tc.function.name, tc.function.arguments)
            data = json.loads(result)
            if tc.function.name == "search_documents" and "results" in data:
                searches += 1
                for name in data["recommended_files"]:
                    if name not in candidates:
                        candidates.append(name)
            if tc.function.name == "read_document" and isinstance(
                data.get("text"), str
            ):
                name = data["filename"]
                if name not in files_read:
                    files_read.append(name)
            if verbose:
                print(
                    f"[round {round_no}] {tc.function.name}({tc.function.arguments}) "
                    f"-> {len(result.encode())} bytes",
                    file=sys.stderr,
                )
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

    return "(Stopped: too many tool rounds without a final answer.)", files_read


def _ask(question: str, verbose: bool) -> None:
    answer, files = run_agent(question, verbose=verbose)
    print(f"\n{answer}\n")
    print(f"Files read: {', '.join(files) if files else '(none)'}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Local NAIS tool-calling agent")
    parser.add_argument("question", nargs="*", help="question (omit for interactive)")
    parser.add_argument("-q", "--quiet", action="store_true", help="hide tool-call log")
    args = parser.parse_args()
    verbose = not args.quiet
    if args.question:
        _ask(" ".join(args.question), verbose)
        return
    while True:
        try:
            q = input("question> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if q:
            _ask(q, verbose)


if __name__ == "__main__":
    main()
