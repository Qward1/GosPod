"""HTTP API раздела «Настройки» — управление учётными записями сотрудников.

Доступен ТОЛЬКО администратору (`require_admin`). Эндпоинты под
/api/web/settings/employees/*. Данные лежат в отдельной БД (см. employees.py).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from app.auth.deps import require_admin
from app.auth.employees import EmployeeStore

router = APIRouter(
    prefix="/api/web/settings",
    tags=["settings"],
    dependencies=[Depends(require_admin)],
)


def _employees(request: Request) -> EmployeeStore:
    auth = getattr(request.app.state, "auth", None)
    store = getattr(auth, "employees", None) if auth else None
    if store is None:
        raise HTTPException(status_code=503, detail="Хранилище сотрудников недоступно")
    return store


class EmployeeCreate(BaseModel):
    name: str
    login: str
    password: str
    position: str = ""
    phone: str = ""


class EmployeeUpdate(BaseModel):
    name: str | None = None
    login: str | None = None
    password: str | None = None
    position: str | None = None
    phone: str | None = None
    active: bool | None = None


@router.get("/employees")
async def list_employees(request: Request) -> dict:
    return {"items": _employees(request).list_all()}


@router.post("/employees")
async def create_employee(request: Request, body: EmployeeCreate) -> dict:
    try:
        emp = _employees(request).create(
            body.name, body.login, body.password, body.position, body.phone
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return {"ok": True, "employee": emp}


@router.put("/employees/{emp_id}")
async def update_employee(request: Request, emp_id: int, body: EmployeeUpdate) -> dict:
    try:
        emp = _employees(request).update(
            emp_id,
            name=body.name, login=body.login, password=body.password,
            position=body.position, phone=body.phone, active=body.active,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    if emp is None:
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    return {"ok": True, "employee": emp}


@router.delete("/employees/{emp_id}")
async def delete_employee(request: Request, emp_id: int) -> dict:
    if not _employees(request).delete(emp_id):
        raise HTTPException(status_code=404, detail="Сотрудник не найден")
    return {"ok": True}
