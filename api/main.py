import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

import automode_sync
import sprache
from database import AsyncSessionLocal, engine, get_db
from models import Base, ChecklistTemplate, ChecklistItem
from routers import recordings, health, settings, streaming, checklists, sessions, segments, uploads, service, insights, konfiguration
import typen

BASE_DIR = Path(__file__).parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    # Gottesdienst- und Beitragsarten einmal laden. Danach hält typen.py sie im Speicher
    # und meldet jede Änderung an die Template-Globals weiter.
    async with AsyncSessionLocal() as db:
        await typen.neu_laden(db)
    # Dem Audio-Dienst gleich mitgeben, was er für die Erkennung braucht. Ist er noch
    # nicht da, holt die Hintergrundschleife es nach.
    await typen.an_audio_dienst_schieben()
    # Überträgt erkannte Beiträge in Marker — tut nichts, solange der Automodus aus ist.
    sync = asyncio.create_task(automode_sync.schleife())
    yield
    sync.cancel()


app = FastAPI(title="Akouo API", version="1.0.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

from vorlagen import templates   # die eine Jinja-Umgebung, siehe api/vorlagen.py


# ─── Sprache ────────────────────────────────────────────────────────────────
# Die Sprache steht nicht im Template-Kontext, sondern in einer ContextVar, die diese
# Middleware je Request setzt. Dadurch bleiben alle TemplateResponse-Aufrufe unveraendert
# und niemand kann vergessen, die Sprache mitzugeben — ein vergessener Aufruf faellt sonst
# erst auf, wenn jemand genau diese Seite auf Russisch oeffnet.
#
# Bewusst eine reine ASGI-Middleware und NICHT @app.middleware("http"): Letztere ist eine
# BaseHTTPMiddleware, und die ruft den nachgelagerten Teil in einer eigenen Task auf. Eine
# davor gesetzte ContextVar kommt dort nicht an — die Oberflaeche blieb deutsch, obwohl das
# Cookie richtig stand. Hier laeuft alles in derselben Task.
class SpracheMiddleware:
    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            sprache.setzen(sprache.aus_request(Request(scope)))
        await self.app(scope, receive, send)


app.add_middleware(SpracheMiddleware)


@app.get("/sprache/{code}", include_in_schema=False)
async def sprache_waehlen(code: str, request: Request) -> RedirectResponse:
    """Sprache dieses Geraets umstellen und zurueck, wo man war.

    Als Server-Route und nicht als JavaScript: Der Umschalter muss auch dann funktionieren,
    wenn ein Skript haengt — und das Cookie soll der Browser schreiben, nicht die Seite.
    """
    ziel = request.query_params.get("weiter", "/")
    # Nur eigene Pfade. Ohne diese Pruefung waere der Umschalter eine offene
    # Weiterleitung. Der Backslash muss mit: Browser normalisieren ihn nach WHATWG zu
    # "/", aus "/\example.invalid" wird also "//example.invalid" — und damit doch ein
    # fremder Host.
    if not ziel.startswith("/") or ziel.startswith("//") or "\\" in ziel:
        ziel = "/"
    antwort = RedirectResponse(ziel, status_code=303)
    antwort.set_cookie(
        sprache.COOKIE, sprache.gueltig(code),
        max_age=60 * 60 * 24 * 365, samesite="lax", httponly=False,
    )
    return antwort



app.include_router(health.router)
app.include_router(recordings.router)
app.include_router(settings.router)
app.include_router(streaming.router)
app.include_router(checklists.router)
app.include_router(sessions.router)
app.include_router(segments.router)
app.include_router(uploads.router)
app.include_router(service.router)
app.include_router(konfiguration.router)
app.include_router(insights.router)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    """Browsers ask for this by default — without it every page logs a 404."""
    return FileResponse(BASE_DIR / "static" / "icon-180.png", media_type="image/png")


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request},   # service_types kommt als Template-Global
    )


@app.get("/checkliste", response_class=HTMLResponse)
async def checklist_run_page(request: Request):
    """The preparation step between picking a service and the dashboard.

    Renders empty and fills itself from /api/checklists/active — no server-side state here,
    so the page also works when the operator lands on it with no checklist mapped.
    """
    return templates.TemplateResponse("checkliste.html", {"request": request})


@app.get("/archiv", response_class=HTMLResponse)
async def archiv_page(request: Request):
    """Frühere Aufnahmen ansehen und nachbearbeiten. Füllt sich selbst über
    /api/sessions/archiv — hier wird nichts vorberechnet."""
    return templates.TemplateResponse("archiv.html", {"request": request})


@app.get("/einstellungen", include_in_schema=False)
async def einstellungen_umleitung():
    """Deutsche Adresse, weitergeleitet auf die kanonische.

    Zwei Routen auf dasselbe Template wären fast gleich, aber nicht ganz: die
    Navigation hebt den Menüpunkt anhand des Pfades hervor und markiert nur /settings.
    """
    return RedirectResponse("/settings", status_code=307)


@app.get("/settings", response_class=HTMLResponse)
async def einstellungen_page(request: Request):
    """Alles, was einstellbar ist, an einer Stelle.

    Füllt sich selbst aus /api/konfiguration — kein serverseitiger Zustand, dieselbe
    Bauart wie checkliste.html. Die Zahlen- und Schalterfelder werden aus der
    Felderliste erzeugt, die der Server mitliefert: eine neue Einstellung braucht
    deshalb nur einen Eintrag in api/einstellungen.py und kein neues Formular.

    /settings bleibt als Adresse bestehen, weil die Navigation und ältere Lesezeichen
    darauf zeigen.
    """
    return templates.TemplateResponse("einstellungen.html", {"request": request})


@app.get("/checklists", response_class=HTMLResponse)
async def checklists_page(request: Request, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(ChecklistTemplate).order_by(ChecklistTemplate.created_at.desc())
    )
    templates_list = result.scalars().all()

    templates_data = []
    for tpl in templates_list:
        items_result = await db.execute(
            select(ChecklistItem)
            .where(ChecklistItem.template_id == tpl.id)
            .order_by(ChecklistItem.sort_order)
        )
        items = items_result.scalars().all()
        templates_data.append({
            "id": tpl.id,
            "name": tpl.name,
            "item_list": [{"id": i.id, "text": i.text, "sort_order": i.sort_order} for i in items],
            "item_count": len(items),
        })
    return templates.TemplateResponse(
        "checklists.html",
        {"request": request, "templates": templates_data}
    )
