"""Shared string enums for AI graphs, guardrails, and streaming.

Graph edge labels still use plain ``.value`` strings so LangGraph routing
stays compatible with existing checkpoints.
"""

from __future__ import annotations

from enum import StrEnum


class Audience(StrEnum):
    STUDENT = "student"
    ADMIN = "admin"


class GuardSide(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    TOOL = "tool"


class GuardAction(StrEnum):
    PASS = "pass"
    BLOCK = "block"
    SUPPORT = "support"


class RunStatus(StrEnum):
    RUNNING = "running"
    PAUSED = "paused"
    FINISHED = "finished"


class TutorRoute(StrEnum):
    CHITCHAT = "chitchat"
    SQL = "sql"
    RAG = "rag"
    ACTION = "action"


class AdminRoute(StrEnum):
    CHITCHAT = "chitchat"
    WORK = "work"


class StreamEvent(StrEnum):
    STATUS = "status"
    TOOL = "tool"
    TOKEN = "token"
    UI = "ui"
    USER_MESSAGE = "userMessage"
    ASSISTANT_MESSAGE = "assistantMessage"
    ACTION_PROPOSAL = "actionProposal"
    PROPOSAL_SETTLED = "proposalSettled"
    DONE = "done"
    STREAM_STARTED = "streamStarted"
