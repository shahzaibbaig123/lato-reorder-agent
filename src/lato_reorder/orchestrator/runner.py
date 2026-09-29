"""Start, pause and resume reorder runs (LangGraph + durable SQLite checkpointer)."""

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import TracebackType
from typing import Any

import aiosqlite
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.types import Command

from lato_reorder.orchestrator.graph import Deps, ReorderState, build_graph


@dataclass
class RunStatus:
    run_id: str
    waiting_for_review: bool
    done: bool
    values: dict[str, Any] = field(default_factory=dict)
    pending: list[str] = field(default_factory=list)


class Orchestrator:
    """Async context manager owning the compiled graph and its checkpointer connection."""

    def __init__(self, deps: Deps, checkpoints_path: Path | str) -> None:
        self.deps = deps
        self.checkpoints_path = str(checkpoints_path)
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "Orchestrator":
        if self.checkpoints_path != ":memory:":
            Path(self.checkpoints_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.checkpoints_path)
        saver = AsyncSqliteSaver(self._conn)
        await saver.setup()
        self.graph = build_graph(self.deps).compile(checkpointer=saver)
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        if self._conn is not None:
            await self._conn.close()

    @staticmethod
    def _config(run_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": run_id}}

    async def start(self, run_id: str | None = None) -> RunStatus:
        run_id = run_id or uuid.uuid4().hex[:12]
        initial: ReorderState = {"run_id": run_id, "mode": self.deps.mode.value}
        await self.graph.ainvoke(initial, self._config(run_id))
        return await self.status(run_id)

    async def resume(self, run_id: str, approver: str) -> RunStatus:
        status = await self.status(run_id)
        if not status.waiting_for_review:
            return status
        resume: Command[Any] = Command(resume={"approver": approver})
        await self.graph.ainvoke(resume, self._config(run_id))
        return await self.status(run_id)

    async def status(self, run_id: str) -> RunStatus:
        snap = await self.graph.aget_state(self._config(run_id))
        waiting = "human_review" in (snap.next or ())
        pending: list[str] = []
        for task in snap.tasks or ():
            for intr in task.interrupts or ():
                if isinstance(intr.value, dict):
                    pending = list(intr.value.get("pending", []))
        values = dict(snap.values or {})
        return RunStatus(run_id, waiting, values.get("status") == "DONE", values, pending)
