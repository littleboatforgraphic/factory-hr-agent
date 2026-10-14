from hr_agent.storage.base import Store


class PostgresStore(Store):  # pragma: no cover - placeholder
    """Placeholder: PostgreSQL backend for larger factories (planned)."""

    def __init__(self, *_args, **_kwargs):
        raise NotImplementedError("PostgreSQL storage is planned; use backend: excel for now")
