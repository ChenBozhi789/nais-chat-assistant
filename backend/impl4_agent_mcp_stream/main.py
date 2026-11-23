"""Implementation 4: Foundry agent + remote MCP, with streamed replies.

Run from the repo root: ``uvicorn backend.impl4_agent_mcp_stream.main:app --reload``
"""

from backend.app_factory import create_app
from backend.impl4_agent_mcp_stream.answer import answer_question, stream_answer

app = create_app(answer_question, stream_answer)
