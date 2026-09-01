from typing import Any


def log(event: str, **fields: Any) -> None:
    print({"type": event, **fields})
