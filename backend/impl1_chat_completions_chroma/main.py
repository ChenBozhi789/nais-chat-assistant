"""Implementation 1: Foundry chat completions + Chroma RAG.

Run from the repo root: ``uvicorn backend.impl1_chat_completions_chroma.main:app --reload``
"""

from backend.app_factory import create_app
from backend.impl1_chat_completions_chroma.answer import answer_question

app = create_app(answer_question)
