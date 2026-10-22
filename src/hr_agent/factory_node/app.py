"""Factory node API. Runs on the factory's own server; payroll data never leaves except via these calls.

Env:
  HR_FACTORY_CONFIG  path to the factory YAML
  HR_NODE_TOKENS     path to a YAML of {token: {user, role, employee_id?}}; roles: hr, accountant, management, agent, employee
"""

from __future__ import annotations

import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path

import yaml
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
from pydantic import BaseModel

from hr_agent import service
from hr_agent.config import FactoryConfig, load_factory
from hr_agent.models import LeaveCredit, LeaveType
from hr_agent.storage import RunLockedError, open_store
from hr_agent.storage.base import Store


class Caller(BaseModel):
    user: str
    role: str
    employee_id: str | None = None


class LinkRequest(BaseModel):
    channel: str = "telegram"
    chat_id: str
    phone: str


STAFF = {"hr", "accountant", "management"}
READERS = STAFF | {"agent"}


def create_app(factory: FactoryConfig, store: Store, tokens: dict[str, dict]) -> FastAPI:
    app = FastAPI(title=f"HR factory node - {factory.id}")

    def caller(authorization: str = Header(default="")) -> Caller:
        token = authorization.removeprefix("Bearer ").strip()
        info = tokens.get(token)
        if not token or info is None:
            raise HTTPException(401, "Invalid token")
        return Caller(**info)

    def require(roles: set[str]):
        def dep(c: Caller = Depends(caller)) -> Caller:
            if c.role not in roles:
                raise HTTPException(403, f"Role {c.role} not allowed")
            return c

        return dep

    def get_run(period: str, version: int | None = None):
        run = store.load_run(period, version)
        if run is None:
            raise HTTPException(404, f"No payroll run for {period}")
        return run

    @app.get("/health")
    def health():
        return {"factory_id": factory.id, "storage": factory.storage.backend, "ok": True}

    @app.post("/attendance/import")
    def import_attendance(profile: str = Form(...), file: UploadFile = File(...), c: Caller = Depends(require({"hr"}))):
        suffix = Path(file.filename or "upload").suffix
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix=Path(file.filename or "upload").stem + "_") as tmp:
            shutil.copyfileobj(file.file, tmp)
        try:
            return service.import_attendance(factory, store, tmp.name, profile, c.user)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        finally:
            os.unlink(tmp.name)

    @app.post("/runs/{period}")
    def create_run(period: str, c: Caller = Depends(require({"hr"}))):
        try:
            return service.run_summary(service.run_payroll(factory, store, period, c.user))
        except RunLockedError as e:
            raise HTTPException(409, str(e)) from e

    @app.get("/runs/{period}")
    def summary(period: str, version: int | None = None, c: Caller = Depends(require(READERS))):
        return service.run_summary(get_run(period, version))

    @app.get("/runs/{period}/flags")
    def flags(period: str, version: int | None = None, c: Caller = Depends(require(READERS))):
        return [f.model_dump(mode="json") for f in get_run(period, version).flags]

    @app.post("/runs/{period}/{version}/{target}")
    def transition(period: str, version: int, target: str, note: str = "", c: Caller = Depends(require(STAFF))):
        try:
            run = service.transition(store, period, version, target, c.user, c.role, note)  # type: ignore[arg-type]
        except PermissionError as e:
            raise HTTPException(403, str(e)) from e
        except (ValueError, RunLockedError) as e:
            raise HTTPException(409, str(e)) from e
        return service.run_summary(run)

    @app.post("/leave/replacement")
    def replacement_leave(credit: LeaveCredit, on_behalf_of: str = "", c: Caller = Depends(require({"hr", "agent"}))):
        """HR records replacement leave; the agent may do so only on a named HR officer's instruction."""
        if c.role == "agent":
            hr_users = {i["user"] for i in tokens.values() if i.get("role") == "hr"}
            if on_behalf_of not in hr_users:
                raise HTTPException(403, "The agent can record leave only on behalf of an HR officer")
        else:
            on_behalf_of = ""
        try:
            saved = service.record_leave_credit(store, credit.model_copy(update={"leave_type": LeaveType.replacement}),
                                                c.user, on_behalf_of)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return saved.model_dump(mode="json")

    @app.get("/directory/employee")
    def find_employee(telegram_id: str = "", phone: str = "", c: Caller = Depends(require({"agent"}))):
        """Lets the cloud agent recognise an employee chatting; returns only the id and language."""
        e = service.find_employee(store, telegram_id=telegram_id, phone=phone)
        if e is None:
            raise HTTPException(404, "Not a current employee")
        return {"employee_id": e.employee_id, "language": e.language}

    @app.post("/directory/link")
    def link(req: LinkRequest, c: Caller = Depends(require({"agent"}))):
        if req.channel != "telegram":
            raise HTTPException(400, "Only Telegram accounts are linked; WhatsApp uses the phone number")
        e = service.link_chat(store, req.channel, req.chat_id, req.phone, c.user)
        if e is None:
            raise HTTPException(404, "Phone number does not match a current employee")
        return {"employee_id": e.employee_id, "language": e.language}

    @app.get("/runs/{period}/payslips/{employee_id}")
    def payslip(period: str, employee_id: str, version: int | None = None, released_only: bool = False,
                c: Caller = Depends(caller)):
        if c.role == "employee":
            if c.employee_id != employee_id:
                raise HTTPException(403, "Employees can only view their own payslip")
        elif c.role not in READERS:
            raise HTTPException(403, "Not allowed")
        run = get_run(period, version)
        if (c.role == "employee" or released_only) and run.status != "locked":
            raise HTTPException(404, "Payslip not yet released")
        for p in run.payslips:
            if p.employee_id == employee_id:
                data = p.model_dump(mode="json", exclude={"days"} if c.role in ("agent", "employee") else None)
                data["run_status"] = run.status
                return data
        raise HTTPException(404, "Employee not in this run")

    return app


@lru_cache
def _default_app() -> FastAPI:
    factory = load_factory(os.environ["HR_FACTORY_CONFIG"])
    tokens = yaml.safe_load(Path(os.environ["HR_NODE_TOKENS"]).read_text()) or {}
    return create_app(factory, open_store(factory), tokens)


def app_factory() -> FastAPI:
    """uvicorn hr_agent.factory_node.app:app_factory --factory"""
    return _default_app()
