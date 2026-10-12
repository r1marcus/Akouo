from datetime import datetime
from typing import Optional
from sqlalchemy import Boolean, ForeignKey, Integer, String, DateTime, Float as sa_Float, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Recording(Base):
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    date: Mapped[str] = mapped_column(String, nullable=False)          # "YYYY-MM-DD"
    service_type: Mapped[str] = mapped_column(String, nullable=False)
    duration: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ChecklistTemplate(Base):
    __tablename__ = "checklist_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChecklistItem(Base):
    __tablename__ = "checklist_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("checklist_templates.id"), nullable=False
    )
    text: Mapped[str] = mapped_column(String, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class ChecklistInstance(Base):
    __tablename__ = "checklist_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("checklist_templates.id"), nullable=False
    )
    service_date: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class ChecklistCheck(Base):
    __tablename__ = "checklist_checks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instance_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("checklist_instances.id"), nullable=False
    )
    item_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("checklist_items.id"), nullable=False
    )
    checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class RecordingSession(Base):
    __tablename__ = "recording_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    paused_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    wav_path: Mapped[str] = mapped_column(String, nullable=False)
    service_type: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="recording")  # recording|paused|stopped
    couple_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    wedding_part: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Freier Titel des Gottesdienstes ("Erntedankfest", "Taufe Familie Müller").
    # Landet im Dateinamen und im Ordnernamen auf dem NAS.
    thema: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Folder this session's files go to on the NAS, relative to the share. Decided once on
    # the first upload and kept, so every later segment lands next to its siblings even when
    # the obvious name was already taken and a "2. Teil" folder had to be used.
    nas_folder: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class Marker(Base):
    __tablename__ = "markers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recording_sessions.id"), nullable=False
    )
    marker_type: Mapped[str] = mapped_column(String, nullable=False)
    placed_at_ts: Mapped[float] = mapped_column(sa_Float, nullable=False)
    placed_at_wall: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    note: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    start_nudge: Mapped[float] = mapped_column(sa_Float, nullable=False, default=0.0)
    end_nudge: Mapped[float] = mapped_column(sa_Float, nullable=False, default=0.0)
    # Set once the operator nudges by hand: the boundary automation must then leave this
    # marker alone instead of resetting the offsets to its own values.
    nudge_manual: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    ended_at_ts: Mapped[Optional[float]] = mapped_column(sa_Float, nullable=True)  # track end (None = still open)
    audio_service_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class SegmentInsight(Base):
    """What the local language model extracted from a segment's transcript."""
    __tablename__ = "segment_insights"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("recording_sessions.id"), nullable=False)
    marker_id: Mapped[int] = mapped_column(Integer, ForeignKey("markers.id"), nullable=False, unique=True)
    title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    summary: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bible_refs: Mapped[Optional[str]] = mapped_column(String, nullable=True)   # JSON list
    keywords: Mapped[Optional[str]] = mapped_column(String, nullable=True)     # JSON list
    speaker: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    transcript: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)


class ServiceTypeTemplate(Base):
    __tablename__ = "service_type_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    service_type: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    track_sequence: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # JSON-encoded list
    checklist_template_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("checklist_templates.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class SegmentExport(Base):
    __tablename__ = "segment_exports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("recording_sessions.id"), nullable=False
    )
    marker_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("markers.id"), nullable=False
    )
    # Denormalised copy so list views don't need a join
    marker_type: Mapped[str] = mapped_column(String, nullable=False)
    # Effective start = marker.placed_at_ts + marker.start_nudge
    start_seconds: Mapped[float] = mapped_column(sa_Float, nullable=False)
    end_seconds: Mapped[float] = mapped_column(sa_Float, nullable=False)
    mp3_path: Mapped[str] = mapped_column(String, nullable=False)
    mp3_filename: Mapped[str] = mapped_column(String, nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    nas_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    nas_status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    nas_error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    uploaded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class ServiceType(Base):
    """Gottesdienstarten. Vorher eine Liste in api/service_types.py.

    `geschuetzt` markiert Namen, an denen Logik haengt: "Hochzeit" schaltet die
    Brautpaar-Felder und das NAS-Layout Hochzeiten/<Jahr>/<Brautpaar> frei,
    "Sonstiges" ein eigenes Layout. Solche Zeilen darf die Oberflaeche umsortieren
    und ausblenden, aber nicht umbenennen oder loeschen.
    """
    __tablename__ = "service_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Die vier grossen Kacheln auf der Startseite
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Ausgeblendet statt geloescht: alte Aufnahmen verweisen per Name darauf
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    geschuetzt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Vorlage fuer den NAS-Ordner; leer heisst: die allgemeine Vorlage aus den
    # Einstellungen benutzen.
    nas_vorlage: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class MarkerType(Base):
    """Beitragsarten eines Gottesdienstes — die Knoepfe waehrend der Aufnahme.

    Haelt auch das, was die Erkennung ueber einen Typ wissen muss. Damit funktioniert
    die Automatik fuer einen selbst angelegten Typ mit, statt ihn zu ignorieren:

    * `klang_klasse`  "musik" oder "sprache" — woraufhin die Klangmerkmale pruefen,
                      ob der Typ zur gemessenen Klasse passt. Leer heisst: entsteht
                      nur, wenn er angesagt wird oder von Hand gesetzt (ein Gebetslied
                      klingt wie ein Chorlied, Raten waere schlimmer als Schweigen).
    * `anker`         Woran sich die Zuordnung des angesagten Programms festmacht.
                      An einem Anker gilt alles davor als erledigt.
    * `auto_marker`   Darf die Automatik dafuer von sich aus einen Marker anlegen?
    * `dauer_min/max` Typische Laenge in Sekunden, als Vermutung — nicht als Vorhersage.

    `geschuetzt` markiert Namen, die in den Ansage-Regeln vorkommen. Umbenennen
    wuerde die Erkennung still aufhoeren lassen.
    """
    __tablename__ = "marker_types"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Geht dieser Typ beim Sammel-Export mit? Einzeln exportieren bleibt immer moeglich.
    exportieren: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    klang_klasse: Mapped[Optional[str]] = mapped_column(String, nullable=True)  # musik|sprache|None
    anker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_marker: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dauer_min: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dauer_max: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    geschuetzt: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class AnsageRegel(Base):
    """Wie eine Ansage im Transkript auf einen Beitragstyp schliessen laesst.

    Vorher eine Liste kompilierter regulaerer Ausdruecke in classifier.py. Als Daten,
    weil jede Gemeinde anders ansagt — "unser Chor singt" trifft nicht ueberall.
    Die Reihenfolge entscheidet: die erste passende Regel gewinnt.
    """
    __tablename__ = "ansage_regeln"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    muster: Mapped[str] = mapped_column(String, nullable=False)        # regulaerer Ausdruck
    marker_type: Mapped[str] = mapped_column(String, nullable=False)   # Name, nicht Fremdschluessel
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )
