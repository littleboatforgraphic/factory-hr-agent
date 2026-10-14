from hr_agent.storage.base import Store


class SQLiteStore(Store):  # pragma: no cover - placeholder
    """Placeholder: single-file SQLite backend (planned)."""

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError("SQLite storage is planned; use backend: excel for now")
