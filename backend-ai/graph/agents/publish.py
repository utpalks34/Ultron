"""Token publishing for nodes that stream their own answer (web and dev agents). The
orchestrator sends the final done marker when the node succeeds; a node that fails calls
close() so the chat bubble is not left open."""


class TokenPublisher:
    def __init__(self, bus, run_id: str | None = None):
        self.bus = bus
        self.run_id = run_id
        self.started = False

    async def _publish(self, text: str, done: bool) -> None:
        evt = {"type": "token", "payload": {"text": text, "role": "assistant", "done": done}}
        if self.run_id:
            evt["run_id"] = self.run_id
        await self.bus.publish(evt)

    async def token(self, text: str) -> None:
        if not text:
            return
        self.started = True
        await self._publish(text, False)

    async def close(self) -> None:
        if self.started:
            await self._publish("", True)
