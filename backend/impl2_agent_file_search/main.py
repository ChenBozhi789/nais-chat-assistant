"""Implementation 2: Foundry agent + File Search.

Run from the repo root: ``uvicorn backend.impl2_agent_file_search.main:app --reload``
"""

from backend.app_factory import create_app
from backend.impl2_agent_file_search.answer import answer_question

app = create_app(answer_question)
