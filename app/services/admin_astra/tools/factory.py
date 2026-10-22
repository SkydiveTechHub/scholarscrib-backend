"""Register read tools and approval-gated mutations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field, ValidationError, create_model

from app.services.admin_astra.compact import dumps, error_payload
from app.services.admin_astra.context import AdminToolContext, get_admin_tool_context
from app.services.admin_astra.tools.registry import AdminToolSpec, register_admin_tool
from app.services.rag.tools.hitl import encode_proposal


class EmptyArgs(BaseModel):
    """No arguments."""


def register_read[ArgsT: BaseModel](
    name: str,
    domain: str,
    description: str,
    args_model: type[ArgsT],
    runner: Callable[[AdminToolContext, ArgsT], Awaitable[Any]],
    *,
    owner_only: bool = False,
) -> None:
    async def _call(**kwargs: object) -> str:
        try:
            parsed = cast(ArgsT, args_model.model_validate(kwargs))
            data = await runner(get_admin_tool_context(), parsed)
        except Exception as exc:  # noqa: BLE001
            return error_payload(exc)
        return dumps(data)

    tool = StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=description,
        args_schema=args_model,
    )
    register_admin_tool(
        AdminToolSpec(
            name=name,
            domain=domain,
            mutating=False,
            owner_only=owner_only,
            destructive=False,
            tool=tool,
            args_model=args_model,
            execute=None,
        )
    )


def register_mutation[ArgsT: BaseModel](
    name: str,
    domain: str,
    description: str,
    args_model: type[ArgsT],
    runner: Callable[[AdminToolContext, ArgsT], Awaitable[Any]],
    *,
    owner_only: bool = False,
    destructive: bool = False,
) -> None:
    schema = create_model(
        f"{args_model.__name__}Card",
        __base__=args_model,
        summary=(
            str,
            Field(
                min_length=1,
                max_length=2000,
                description=(
                    "Approval card. What will change, in names rather than raw "
                    "ids, and what happens if the admin approves."
                ),
            ),
        ),
    )

    async def _call(**kwargs: object) -> str:
        try:
            parsed = schema.model_validate(kwargs)
        except ValidationError as exc:
            message = exc.errors()[0].get("msg", "Invalid arguments")
            return dumps({"ok": False, "error": message})
        payload = parsed.model_dump(mode="json")
        summary = payload.pop("summary")
        return encode_proposal(
            {
                "tool": name,
                "args": payload,
                "summary": summary,
                "destructive": destructive,
                "ownerOnly": owner_only,
            }
        )

    async def _execute(ctx: AdminToolContext, args: Any) -> Any:
        parsed = cast(ArgsT, args_model.model_validate(args))
        return await runner(ctx, parsed)

    tool = StructuredTool.from_function(
        coroutine=_call,
        name=name,
        description=description,
        args_schema=schema,
    )
    register_admin_tool(
        AdminToolSpec(
            name=name,
            domain=domain,
            mutating=True,
            owner_only=owner_only,
            destructive=destructive,
            tool=tool,
            args_model=args_model,
            execute=_execute,
        )
    )
