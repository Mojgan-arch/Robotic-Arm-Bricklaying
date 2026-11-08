"""Stream actual worker events to a UI without reading simulator evaluation data."""
from collections.abc import Callable, Iterator
from queue import Queue, Empty
from threading import Thread, Event
from typing import Any
from time import monotonic
from masonmind.interaction import ClarificationRequired


def stream_work(work: Callable[[Callable[[dict], None]], Any]) -> Iterator[dict]:
    """Run one task and relay ordered events, errors and its final result."""
    queue: Queue = Queue()
    cancelled = Event()

    def publish(event: dict) -> None:
        if cancelled.is_set():
            raise RuntimeError("UI run was cancelled")
        queue.put(event)

    def worker() -> None:
        try:
            result = work(publish)
            queue.put({"kind": "result", "value": result})
        except ClarificationRequired as error:
            queue.put({"kind": "input_required", "questions": error.questions})
        except Exception as error:
            queue.put({"kind": "error", "message": f"{type(error).__name__}: {error}"})

    thread = Thread(target=worker, daemon=True)
    thread.start()
    started = monotonic()
    try:
        while True:
            try:
                event = queue.get(timeout=1)
            except Empty:
                yield {"kind": "heartbeat", "elapsed_s": int(monotonic() - started)}
                continue
            yield event
            if event["kind"] in {"result", "error", "input_required"}:
                break
    finally:
        cancelled.set()
