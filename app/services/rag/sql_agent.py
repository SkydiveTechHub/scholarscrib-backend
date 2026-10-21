"""Read-only SQL subgraph over curriculum catalogue tables."""

from __future__ import annotations

import re
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.logger import logger
from app.services.rag.checkpointer import _psycopg_url
from app.services.rag.llm import get_chat_model

# Prisma-style table names (quoted identifiers in Postgres).
ALLOWED_TABLES = (
    "Subject",
    "Topic",
    "Subtopic",
    "Lesson",
    "CurriculumLevel",
    "SubjectResource",
    "TopicEdge",
)

_WRITE_RE = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|CREATE|GRANT|REVOKE|"
    r"COPY|CALL|EXECUTE|MERGE|REPLACE)\b",
    re.IGNORECASE,
)


def _sync_db_url() -> str:
    return _psycopg_url()


def _get_sql_database():
    from langchain_community.utilities import SQLDatabase

    return SQLDatabase.from_uri(
        _sync_db_url(),
        include_tables=list(ALLOWED_TABLES),
        sample_rows_in_table_info=2,
        view_support=False,
    )


class SqlQueryInput(BaseModel):
    query: str = Field(description="A single read-only SELECT SQL statement")


def _assert_read_only(query: str) -> str:
    cleaned = (query or "").strip().rstrip(";")
    if not cleaned:
        raise ValueError("Empty SQL")
    if ";" in cleaned:
        raise ValueError("Only a single SQL statement is allowed")
    if _WRITE_RE.search(cleaned):
        raise ValueError("Only SELECT queries are permitted")
    if not re.match(r"^\s*(WITH|SELECT)\b", cleaned, re.IGNORECASE):
        raise ValueError("Query must start with SELECT or WITH")
    return cleaned


def _build_tools():
    db = _get_sql_database()

    def list_tables() -> str:
        return ", ".join(ALLOWED_TABLES)

    def table_schema(tables: str) -> str:
        names = [t.strip() for t in tables.split(",") if t.strip()]
        for name in names:
            if name not in ALLOWED_TABLES:
                allowed = ", ".join(ALLOWED_TABLES)
                return f"Table not allowed: {name}. Allowed: {allowed}"
        return db.get_table_info_no_throw(names)

    def run_select(query: str) -> str:
        safe = _assert_read_only(query)
        result = db.run_no_throw(safe)
        return result if isinstance(result, str) else str(result)

    return [
        StructuredTool.from_function(
            name="list_curriculum_tables",
            description="List curriculum tables available for read-only queries.",
            func=list_tables,
        ),
        StructuredTool.from_function(
            name="curriculum_table_schema",
            description=(
                "Get schema for one or more curriculum tables (comma-separated names)."
            ),
            func=table_schema,
        ),
        StructuredTool.from_function(
            name="run_readonly_sql",
            description=(
                "Run a single read-only SELECT against curriculum tables. "
                "Never modify data."
            ),
            func=run_select,
            args_schema=SqlQueryInput,
        ),
    ]


SQL_SYSTEM = (
    "You are a read-only curriculum catalogue assistant for ScholarsCrib. "
    "Answer questions about subjects, topics, lessons, and curriculum structure "
    "using the SQL tools. Only SELECT queries. Prefer small result sets "
    "(LIMIT 25). If the question is not about curriculum structure, say so."
)


class SqlAgentState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


def build_sql_subgraph():
    tools = _build_tools()
    model = get_chat_model(settings.effective_grader_model).bind_tools(tools)

    def agent(state: SqlAgentState) -> dict[str, Any]:
        messages = [SystemMessage(content=SQL_SYSTEM), *state["messages"]]
        response = model.invoke(messages)
        return {"messages": [response]}

    graph = StateGraph(SqlAgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition)
    graph.add_edge("tools", "agent")
    return graph.compile()


_sql_graph = None


def get_sql_subgraph():
    global _sql_graph
    if _sql_graph is None:
        try:
            _sql_graph = build_sql_subgraph()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to build SQL subgraph")
            raise
    return _sql_graph


def run_sql_agent(question: str) -> str:
    """Invoke the SQL subgraph and return the final assistant text."""
    graph = get_sql_subgraph()
    result = graph.invoke(
        {"messages": [HumanMessage(content=question)]},
        {"recursion_limit": 12},
    )
    messages = result.get("messages") or []
    for message in reversed(messages):
        if isinstance(message, AIMessage) and message.content:
            content = message.content
            return content if isinstance(content, str) else str(content)
    return "I could not look up that curriculum information."
