from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func

from database import get_db
from models import ChecklistTemplate, ChecklistItem, ChecklistInstance, ChecklistCheck

BASE_DIR = Path(__file__).parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

router = APIRouter(prefix="/api/checklists", tags=["checklists"])


async def _get_active_data(db: AsyncSession) -> dict:
    """Fetch active checklist instance, its items, and check states.

    Returns a dict with keys: instance, items, unchecked_count.
    """
    # Get most recently created instance
    result = await db.execute(
        select(ChecklistInstance).order_by(ChecklistInstance.created_at.desc()).limit(1)
    )
    instance = result.scalar_one_or_none()

    if instance is None:
        templates_result = await db.execute(
            select(ChecklistTemplate).order_by(ChecklistTemplate.name)
        )
        available = [{"id": t.id, "name": t.name} for t in templates_result.scalars().all()]
        return {"instance": None, "items": [], "unchecked_count": 0, "available_templates": available}

    # Get all items for this instance's template, ordered by sort_order
    items_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.template_id == instance.template_id)
        .order_by(ChecklistItem.sort_order)
    )
    items = items_result.scalars().all()

    # Get all checks for this instance
    checks_result = await db.execute(
        select(ChecklistCheck).where(ChecklistCheck.instance_id == instance.id)
    )
    checks = checks_result.scalars().all()

    # Build check lookup by item_id
    check_by_item = {c.item_id: c for c in checks}

    items_data = []
    unchecked_count = 0
    for item in items:
        check = check_by_item.get(item.id)
        checked = check.checked if check else False
        check_id = check.id if check else None
        items_data.append({
            "id": item.id,
            "text": item.text,
            "sort_order": item.sort_order,
            "checked": checked,
            "check_id": check_id,
        })
        if not checked:
            unchecked_count += 1

    return {
        "instance": instance,
        "items": items_data,
        "unchecked_count": unchecked_count,
    }


@router.get("/active")
async def active_checklist(request: Request, db: AsyncSession = Depends(get_db)):
    """Return the active checklist instance with item check states.

    HX-Request → returns HTML partial for HTMX polling swap.
    Non-HX     → returns JSON.
    """
    data = await _get_active_data(db)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/checklist_section.html",
            {
                "request": request,
                "instance": data["instance"],
                "items": data["items"],
                "unchecked_count": data["unchecked_count"],
                "available_templates": data.get("available_templates", []),
            },
        )
    return {
        "instance": (
            {
                "id": data["instance"].id,
                "template_id": data["instance"].template_id,
                "service_date": data["instance"].service_date,
            }
            if data["instance"]
            else None
        ),
        "items": data["items"],
        "unchecked_count": data["unchecked_count"],
    }


@router.post("/activate/{template_id}")
async def activate_template(template_id: int, db: AsyncSession = Depends(get_db)):
    """Create a new ChecklistInstance for the given template_id with today as service_date.

    Returns the new instance as JSON.
    404 if template_id not found.
    """
    result = await db.execute(
        select(ChecklistTemplate).where(ChecklistTemplate.id == template_id)
    )
    template = result.scalar_one_or_none()
    if template is None:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

    instance = ChecklistInstance(
        template_id=template_id,
        service_date=date.today().isoformat(),
    )
    db.add(instance)
    await db.commit()
    await db.refresh(instance)

    return {
        "id": instance.id,
        "template_id": instance.template_id,
        "service_date": instance.service_date,
        "created_at": instance.created_at.isoformat() if instance.created_at else None,
    }


@router.patch("/items/{item_id}/toggle")
async def toggle_item(
    item_id: int,
    instance_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Toggle the checked state of a checklist item within an instance.

    Creates a ChecklistCheck row if none exists.
    Flips checked bool; sets/clears checked_at accordingly.

    HX-Request → re-renders the full checklist_section partial.
    Non-HX     → returns {"item_id": ..., "checked": ...}.
    """
    result = await db.execute(
        select(ChecklistCheck).where(
            ChecklistCheck.item_id == item_id,
            ChecklistCheck.instance_id == instance_id,
        )
    )
    check = result.scalar_one_or_none()

    if check is None:
        # No row yet — create as checked=True
        check = ChecklistCheck(
            instance_id=instance_id,
            item_id=item_id,
            checked=True,
            checked_at=datetime.utcnow(),
        )
        db.add(check)
    else:
        # Flip existing state
        check.checked = not check.checked
        check.checked_at = datetime.utcnow() if check.checked else None

    await db.commit()
    await db.refresh(check)

    if request.headers.get("HX-Request"):
        data = await _get_active_data(db)
        return templates.TemplateResponse(
            "partials/checklist_section.html",
            {
                "request": request,
                "instance": data["instance"],
                "items": data["items"],
                "unchecked_count": data["unchecked_count"],
            },
        )
    return {"item_id": item_id, "checked": check.checked}


# ---------------------------------------------------------------------------
# Admin CRUD endpoints
# ---------------------------------------------------------------------------

class TemplateCreate(BaseModel):
    name: str


class ItemCreate(BaseModel):
    text: str


class ItemEdit(BaseModel):
    text: str


class MoveDirection(BaseModel):
    direction: str  # "up" or "down"


@router.get("")
async def list_templates(db: AsyncSession = Depends(get_db)):
    """List all checklist templates with item counts, sorted by created_at DESC."""
    result = await db.execute(
        select(ChecklistTemplate).order_by(ChecklistTemplate.created_at.desc())
    )
    templates_list = result.scalars().all()

    output = []
    for tpl in templates_list:
        count_result = await db.execute(
            select(func.count(ChecklistItem.id)).where(
                ChecklistItem.template_id == tpl.id
            )
        )
        item_count = count_result.scalar_one()
        output.append({
            "id": tpl.id,
            "name": tpl.name,
            "created_at": tpl.created_at.isoformat() if tpl.created_at else None,
            "item_count": item_count,
        })
    return output


@router.post("")
async def create_template(
    request: Request,
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
):
    """Create a new checklist template."""
    if not name.strip():
        raise HTTPException(status_code=422, detail="name must be non-empty")

    tpl = ChecklistTemplate(name=name.strip())
    db.add(tpl)
    await db.commit()
    await db.refresh(tpl)

    if request.headers.get("HX-Request"):
        return templates.TemplateResponse(
            "partials/template_row.html",
            {
                "request": request,
                "tpl": {"id": tpl.id, "name": tpl.name, "item_list": [], "item_count": 0},
            },
        )
    return {"id": tpl.id, "name": tpl.name}


@router.post("/{template_id}/items")
async def add_item(
    template_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    text: str = Form(...),
):
    """Add a new item to a template."""
    # Verify template exists
    tpl_result = await db.execute(
        select(ChecklistTemplate).where(ChecklistTemplate.id == template_id)
    )
    if tpl_result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

    if not text.strip():
        raise HTTPException(status_code=422, detail="text must be non-empty")

    # Determine next sort_order
    max_result = await db.execute(
        select(func.max(ChecklistItem.sort_order)).where(
            ChecklistItem.template_id == template_id
        )
    )
    max_order = max_result.scalar_one()
    next_order = (max_order + 1) if max_order is not None else 0

    item = ChecklistItem(
        template_id=template_id,
        text=text.strip(),
        sort_order=next_order,
    )
    db.add(item)
    await db.commit()
    await db.refresh(item)

    item_data = {"id": item.id, "template_id": item.template_id, "text": item.text, "sort_order": item.sort_order}

    if request.headers.get("HX-Request"):
        # Return a single item row HTML for HTMX to append
        return templates.TemplateResponse(
            "partials/item_row.html",
            {
                "request": request,
                "item": {"id": item.id, "text": item.text, "sort_order": item.sort_order},
                "tpl_id": template_id,
            },
        )
    return item_data


@router.patch("/items/{item_id}")
async def edit_item(
    item_id: int,
    request: Request,
    text: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """Update item text."""
    result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    if not text.strip():
        raise HTTPException(status_code=422, detail="text must be non-empty")

    item.text = text.strip()
    await db.commit()
    await db.refresh(item)

    # Return HTML partial for HTMX, JSON otherwise
    if request.headers.get("HX-Request"):
        template_result = await db.execute(
            select(ChecklistTemplate).where(ChecklistTemplate.id == item.template_id)
        )
        template = template_result.scalar_one()
        items_result = await db.execute(
            select(ChecklistItem)
            .where(ChecklistItem.template_id == template.id)
            .order_by(ChecklistItem.sort_order)
        )
        items = items_result.scalars().all()
        items_data = [{"id": i.id, "text": i.text, "sort_order": i.sort_order} for i in items]
        return templates.TemplateResponse(
            "partials/template_row.html",
            {
                "request": request,
                "tpl": {
                    "id": template.id,
                    "name": template.name,
                    "item_list": items_data,
                    "item_count": len(items_data),
                },
            },
        )
    return {"id": item.id, "template_id": item.template_id, "text": item.text, "sort_order": item.sort_order}


@router.delete("/items/{item_id}")
async def delete_item(item_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Delete a checklist item.

    HX-Request → re-renders the template row partial.
    Non-HX     → returns 204.
    """
    result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    template_id = item.template_id
    await db.delete(item)
    await db.commit()

    if request.headers.get("HX-Request"):
        return await _render_template_row(template_id, request, db)

    from fastapi.responses import Response
    return Response(status_code=204)


@router.post("/items/{item_id}/move")
async def move_item(
    item_id: int,
    request: Request,
    db: AsyncSession = Depends(get_db),
    direction: str = Form(...),
):
    """Reorder item by swapping sort_order with adjacent item (up/down).

    HX-Request → re-renders the template row partial.
    Non-HX     → returns JSON.
    """
    if direction not in ("up", "down"):
        raise HTTPException(status_code=422, detail="direction must be 'up' or 'down'")

    result = await db.execute(
        select(ChecklistItem).where(ChecklistItem.id == item_id)
    )
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail=f"Item {item_id} not found")

    template_id = item.template_id

    # Get all items for this template ordered by sort_order
    siblings_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.template_id == template_id)
        .order_by(ChecklistItem.sort_order)
    )
    siblings = siblings_result.scalars().all()

    # Find position of current item in sorted list
    idx = next((i for i, s in enumerate(siblings) if s.id == item_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="Item not found in siblings")

    if direction == "up":
        if idx == 0:
            raise HTTPException(status_code=400, detail="Item is already at the top")
        neighbor = siblings[idx - 1]
    else:
        if idx == len(siblings) - 1:
            raise HTTPException(status_code=400, detail="Item is already at the bottom")
        neighbor = siblings[idx + 1]

    # Swap sort_order values
    item.sort_order, neighbor.sort_order = neighbor.sort_order, item.sort_order
    await db.commit()

    if request.headers.get("HX-Request"):
        return await _render_template_row(template_id, request, db)

    return {"id": item.id, "sort_order": item.sort_order}


async def _render_template_row(template_id: int, request: Request, db: AsyncSession):
    """Helper: fetch template data and render the template_row partial."""
    tpl_result = await db.execute(
        select(ChecklistTemplate).where(ChecklistTemplate.id == template_id)
    )
    tpl = tpl_result.scalar_one_or_none()
    if tpl is None:
        raise HTTPException(status_code=404, detail=f"Template {template_id} not found")

    items_result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.template_id == template_id)
        .order_by(ChecklistItem.sort_order)
    )
    items = items_result.scalars().all()
    items_data = [{"id": i.id, "text": i.text, "sort_order": i.sort_order} for i in items]

    return templates.TemplateResponse(
        "partials/template_row.html",
        {
            "request": request,
            "tpl": {
                "id": tpl.id,
                "name": tpl.name,
                "item_list": items_data,
                "item_count": len(items_data),
            },
        },
    )
