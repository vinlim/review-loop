from review_loop.types.agents import AgentError, PhaseOutput, PhaseRequest
from review_loop.types.result import Err, Ok


class FakeAgent:
    """Returns scripted phase outputs in order and records every request it received."""

    def __init__(self, outputs=None):
        self.outputs = list(outputs or [])
        self.requests: list[PhaseRequest] = []

    def reply(self, data: dict, session_id: str = "sess-fake") -> None:
        self.outputs.append(Ok(PhaseOutput(data, session_id, "/fake/result.json", "/fake/events.jsonl")))

    def fail(self, error: AgentError) -> None:
        self.outputs.append(Err(error))

    def run(self, request: PhaseRequest):
        self.requests.append(request)
        if not self.outputs:
            raise AssertionError(f"FakeAgent has no output left for phase {request.phase}")
        return self.outputs.pop(0)
