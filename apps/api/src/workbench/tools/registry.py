"""Tool registry and dispatcher.

The registry is where the security model meets the agent loop. Two properties
it must guarantee:

* ``catalogue_for(principal)`` returns only tools that principal may use. This
  list becomes the planner's prompt, so an unauthorised tool is not merely
  blocked — the model never learns it exists and cannot plan around it.
* ``dispatch`` re-checks permission at invocation. The catalogue is a
  convenience; the check that matters happens at the point of action, because a
  model can emit a tool name it was never offered.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import yaml

from workbench.core.errors import AuthorizationError, ToolError, ToolNotFoundError
from workbench.core.logging import get_logger
from workbench.security.rbac import Principal
from workbench.tools.base import Tool, ToolContext, ToolResult, ToolSpec

log = get_logger(__name__)


class ToolRegistry:
    """Holds the available tools and enforces access to them."""

    def __init__(self, config_path: Path | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        self._semaphores: dict[str, asyncio.Semaphore] = {}
        self._config: dict[str, Any] = {}
        self._policy: dict[str, Any] = {}
        if config_path and config_path.is_file():
            try:
                raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                self._config = raw.get("tools") or {}
                self._policy = raw.get("policy") or {}
            except yaml.YAMLError as exc:
                log.error("tools_config_invalid", path=str(config_path), error=str(exc))

    # ------------------------------------------------------------ registration
    def register(self, tool: Tool) -> None:
        name = tool.spec.name
        if name in self._tools:
            raise ValueError(f"tool '{name}' is already registered")
        if not self._enabled(name):
            log.info("tool_disabled_by_config", tool=name)
            return
        self._tools[name] = tool
        self._semaphores[name] = asyncio.Semaphore(tool.spec.max_concurrency)

    def register_all(self, *tools: Tool) -> None:
        for tool in tools:
            self.register(tool)

    def _enabled(self, name: str) -> bool:
        return bool((self._config.get(name) or {}).get("enabled", True))

    # ---------------------------------------------------------------- lookup
    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"no tool named '{name}'") from None

    def spec(self, name: str) -> ToolSpec:
        return self.get(name).spec

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def catalogue_for(self, principal: Principal) -> list[ToolSpec]:
        """The tools this principal may use.

        This is what the planner is shown. A tool missing from here cannot be
        planned, which is a stronger guarantee than refusing it afterwards.
        """
        return [
            tool.spec
            for name, tool in sorted(self._tools.items())
            if tool.spec.available_to(principal)
        ]

    def schemas_for(self, principal: Principal) -> list[dict[str, Any]]:
        """The catalogue in the shape a model's tool-calling API expects."""
        return [spec.to_schema().to_openai() for spec in self.catalogue_for(principal)]

    # -------------------------------------------------------------- approval
    def requires_approval(
        self,
        name: str,
        *,
        touched_restricted: bool = False,
        is_engineering_calc: bool = False,
    ) -> bool:
        """Whether this invocation needs a human decision.

        The union of the tool's own default, the config override, and dynamic
        policy. Decided here, never asked of the model.
        """
        spec = self.spec(name)
        configured = (self._config.get(name) or {}).get("requires_approval")
        if configured is not None and bool(configured):
            return True
        if spec.requires_approval:
            return True
        if touched_restricted and self._policy.get("restricted_source_forces_approval", True):
            return True
        return bool(
            is_engineering_calc and self._policy.get("engineering_calc_forces_final_approval", True)
        )

    # -------------------------------------------------------------- dispatch
    async def dispatch(self, name: str, raw_args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        """Validate, authorise and run a tool call.

        Permission is re-checked here even though the catalogue already filtered:
        a model can emit a tool name it was never offered, and this is the point
        where that must fail.
        """
        tool = self.get(name)
        spec = tool.spec

        missing = spec.required_permissions - ctx.principal.permissions
        if missing:
            raise AuthorizationError(
                f"'{name}' requires {', '.join(sorted(missing))}",
                tool=name,
                required_permission=sorted(missing)[0],
            )

        args = (
            tool.parse_args(raw_args)
            if hasattr(tool, "parse_args")
            else spec.input_model.model_validate(raw_args)
        )

        timeout = (
            min(spec.timeout_s, ctx.remaining_s)
            if ctx.remaining_s != float("inf")
            else spec.timeout_s
        )
        if timeout <= 0:
            return ToolResult.failure(f"'{name}' skipped: the run's time budget is exhausted")

        started = time.perf_counter()
        await ctx.trace("tool_call", {"tool": name, "args": self._summarise_args(args)})

        try:
            async with self._semaphores[name]:
                result = await asyncio.wait_for(tool.run(args, ctx), timeout=timeout)
        except TimeoutError:
            result = ToolResult.failure(f"'{name}' exceeded its {timeout:.0f}s timeout")
        except (ToolError, AuthorizationError):
            raise
        except Exception as exc:
            # A tool failing must not kill the run; the agent sees the error and
            # can replan or report honestly.
            log.exception("tool_failed", tool=name, error=str(exc))
            result = ToolResult.failure(f"{type(exc).__name__}: {exc}")

        result.metrics.setdefault("latency_ms", int((time.perf_counter() - started) * 1000))
        await ctx.trace("tool_result", {"tool": name, **result.summary()})
        return result

    @staticmethod
    def _summarise_args(args: Any) -> dict[str, Any]:
        """Compact arguments for the trace, so a long query does not flood it."""
        try:
            payload = args.model_dump(mode="json")
        except AttributeError:
            return {}
        summarised: dict[str, Any] = {}
        for key, value in payload.items():
            if isinstance(value, str) and len(value) > 160:
                summarised[key] = value[:160] + "…"
            elif isinstance(value, list) and len(value) > 8:
                summarised[key] = [*value[:8], f"…{len(value) - 8} more"]
            else:
                summarised[key] = value
        return summarised
