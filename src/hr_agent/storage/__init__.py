from hr_agent.config import FactoryConfig
from hr_agent.storage.base import RunLockedError, Store


def open_store(factory: FactoryConfig) -> Store:
    backend = factory.storage.backend
    path = factory.resolve(factory.storage.path)
    if backend == "excel":
        from hr_agent.storage.excel import ExcelStore

        return ExcelStore(path)
    if backend == "sqlite":
        from hr_agent.storage.sqlite import SQLiteStore

        return SQLiteStore(path)
    if backend == "postgres":
        from hr_agent.storage.postgres import PostgresStore

        return PostgresStore(path)
    raise ValueError(f"Unknown storage backend {backend!r}")


__all__ = ["RunLockedError", "Store", "open_store"]
