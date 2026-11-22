"""Implementation 3: Foundry agent + remote MCP tool calling.

Run from the repo root: ``uvicorn backend.impl3_agent_mcp.main:app --reload``
"""

from backend.app_factory import create_app
from backend.impl3_agent_mcp.answer import answer_question

app = create_app(answer_question)
