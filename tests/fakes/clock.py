from datetime import datetime, timezone


class FakeClock:
    def __init__(self, start: datetime | None = None):
        self.current = start or datetime(2026, 9, 27, 10, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, seconds: int) -> None:
        from datetime import timedelta

        self.current = self.current + timedelta(seconds=seconds)
