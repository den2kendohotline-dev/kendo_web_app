from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from collections import defaultdict
from datetime import date, datetime
from typing import Generator

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    case,
    create_engine,
    inspect,
    select,
    text,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)
from starlette.middleware.sessions import SessionMiddleware

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./kendo.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(20), default="member")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Player(Base):
    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        index=True,
    )

    # 移行が終わるまでは残してよい
    grade: Mapped[str] = mapped_column(
        String(50),
        default="",
    )

    # 入学した年度。例：2024年度入学なら2024
    enrollment_year: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )

    note: Mapped[str] = mapped_column(
        String(255),
        default="",
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
    )


class Tournament(Base):
    __tablename__ = "tournaments"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    event_date: Mapped[date] = mapped_column(Date)
    venue: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    events: Mapped[list["Event"]] = relationship(
        back_populates="tournament_parent",
        cascade="all, delete-orphan",
        order_by="Event.id",
    )


class Event(Base):
    __tablename__ = "events"
    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey("tournaments.id"))
    event_type: Mapped[str] = mapped_column(String(20))

    round_name: Mapped[str] = mapped_column(String(100), default="")
    team_round: Mapped[int] = mapped_column(Integer, default=1)

    opponent_team: Mapped[str] = mapped_column(String(200), default="")
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    tournament_parent: Mapped[Tournament] = relationship(back_populates="events")
    entries: Mapped[list["MatchEntry"]] = relationship(
        back_populates="event",
        cascade="all, delete-orphan",
        order_by="MatchEntry.order_index",
    )


class MatchEntry(Base):
    __tablename__ = "match_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    position: Mapped[str] = mapped_column(String(50), default="")
    player_name: Mapped[str] = mapped_column(String(100))
    opponent_name: Mapped[str] = mapped_column(String(150), default="")
    individual_round: Mapped[str] = mapped_column(String(80), default="")
    result: Mapped[str] = mapped_column(String(20))
    score_for: Mapped[int] = mapped_column(Integer, default=0)
    score_against: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(String(255), default="")
    event: Mapped[Event] = relationship(back_populates="entries")


def ensure_database_schema() -> None:
    """テーブルを作成し、既存DBに不足している列を追加する。"""
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)
    player_columns = {
        column["name"]
        for column in inspector.get_columns("players")
    }

    if "enrollment_year" not in player_columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE players "
                    "ADD COLUMN enrollment_year INTEGER"
                )
            )


ensure_database_schema()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    iterations = 310_000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations_s, salt_hex, digest_hex = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt_hex), int(iterations_s)
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def current_academic_year() -> int:
    """現在の年度を返す。年度は4月1日に切り替わる。"""
    today = date.today()

    if today.month >= 4:
        return today.year

    return today.year - 1


def get_ordered_players(
    db: Session,
) -> list[Player]:
    players = list(
        db.scalars(
            select(Player)
        ).all()
    )

    players.sort(
        key=lambda player: (
            0 if "主将" in (player.note or "") else 1,
            -player_grade_number(player),
            player.name,
        )
    )

    return players


def player_grade_number(player: Player) -> int:
    """現在の学年を数値で返す。未設定なら0。"""
    if player.enrollment_year is None:
        return 0

    return (
        current_academic_year()
        - player.enrollment_year
        + 1
    )


def current_grade(player: Player) -> str:
    """テンプレート表示用の現在学年を返す。"""
    grade_number = player_grade_number(player)

    if 1 <= grade_number <= 4:
        return f"{grade_number}年"

    return ""


def seed_admin() -> None:
    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD", "change-me")
    with SessionLocal() as db:
        if not db.scalar(select(User).where(User.username == username)):
            db.add(
                User(
                    username=username,
                    password_hash=hash_password(password),
                    role="admin",
                )
            )
            db.commit()

def cleanup_graduated_players() -> None:
    with SessionLocal() as db:
        delete_graduated_players(db)


app = FastAPI(title="剣道部 試合結果管理")
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SECRET_KEY", "dev-secret-key-change-me"),
    same_site="lax",
    https_only=False,
)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Environment(
    loader=FileSystemLoader("templates"), autoescape=select_autoescape(["html", "xml"])
)


def render(name: str, **context) -> HTMLResponse:
    return HTMLResponse(templates.get_template(name).render(**context))


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    return db.get(User, int(user_id)) if user_id else None


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401)
    return user


def outcome_text(entry: MatchEntry) -> str:
    if entry.result == "walkover_win":
        return "不戦勝"

    if entry.result == "walkover_loss":
        return "不戦敗"

    if entry.result == "win":
        return f"{entry.score_for}本勝ち"

    if entry.result == "loss":
        return f"{entry.score_against}本負け"

    return "引き分け"


templates.globals["outcome_text"] = outcome_text

templates.globals["current_grade"] = current_grade


TEAM_POSITION_ORDER = {
    "先鋒": 1,
    "次鋒": 2,
    "五将": 3,
    "中堅": 4,
    "三将": 5,
    "副将": 6,
    "大将": 7,
}


def team_entry_order(position: str, fallback: int) -> int:
    """団体戦のポジションを保存用の並び順へ変換する。"""
    return TEAM_POSITION_ORDER.get((position or "").strip(), 100 + fallback)


def sort_team_entries(event: Event) -> None:
    """団体戦の選手をポジション順に並べ、表示順も更新する。"""
    if event.event_type != "team":
        return

    event.entries.sort(
        key=lambda entry: (
            TEAM_POSITION_ORDER.get((entry.position or "").strip(), 999),
            entry.order_index,
            entry.id or 0,
        )
    )

    for index, entry in enumerate(event.entries):
        entry.order_index = index


def delete_graduated_players(
    db: Session,
) -> None:
    players = db.scalars(
        select(Player).where(
            Player.enrollment_year.is_not(None)
        )
    ).all()

    deleted = False

    for player in players:
        if player_grade_number(player) >= 5:
            db.delete(player)
            deleted = True

    if deleted:
        db.commit()


def backfill_enrollment_years(
    db: Session,
) -> None:
    """既存のgradeから入学年度を一度だけ補完する。"""
    grade_numbers = {
        "1年": 1,
        "2年": 2,
        "3年": 3,
        "4年": 4,
    }

    players = db.scalars(
        select(Player).where(
            Player.enrollment_year.is_(None)
        )
    ).all()

    changed = False

    for player in players:
        grade_number = grade_numbers.get(
            (player.grade or "").strip()
        )

        if grade_number is None:
            continue

        player.enrollment_year = (
            current_academic_year()
            - grade_number
            + 1
        )
        changed = True

    if changed:
        db.commit()


with SessionLocal() as startup_db:
    backfill_enrollment_years(startup_db)
    delete_graduated_players(startup_db)

seed_admin()

def tournament_view(
    tournament: Tournament,
    db: Session,
) -> dict:
    individual_by_player: dict[str, list[MatchEntry]] = defaultdict(list)
    team_events: list[dict] = []

    players = db.scalars(
        select(Player)
    ).all()

    player_map = {
        player.name: player
        for player in players
    }

    for event in tournament.events:
        if event.event_type == "team":
            sort_team_entries(event)

        if event.event_type == "individual":
            for entry in event.entries:
                individual_by_player[
                    entry.player_name
                ].append(entry)

        else:
            wins = sum(
                entry.result in {
                    "win",
                    "walkover_win",
                }
                for entry in event.entries
            )

            losses = sum(
                entry.result in {
                    "loss",
                    "walkover_loss",
                }
                for entry in event.entries
            )

            points_for = sum(
                entry.score_for
                for entry in event.entries
            )

            points_against = sum(
                entry.score_against
                for entry in event.entries
            )

            if (
                wins > losses
                or (
                    wins == losses
                    and points_for > points_against
                )
            ):
                result_label = "勝ち"

            elif (
                wins < losses
                or (
                    wins == losses
                    and points_for < points_against
                )
            ):
                result_label = "負け"

            else:
                result_label = "引き分け"

            team_events.append(
                {
                    "event": event,
                    "wins": wins,
                    "losses": losses,
                    "points_for": points_for,
                    "points_against": points_against,
                    "result_label": result_label,
                }
            )

    individual_sections = []

    for player_name, entries in individual_by_player.items():
        entries.sort(
            key=lambda entry: (
                int(entry.individual_round or 0),
                entry.event_id,
                entry.order_index,
            )
        )

        last = entries[-1]

        if last.result in {
            "loss",
            "walkover_loss",
        }:
            heading = (
                f"{last.individual_round or len(entries)}"
                "回戦敗退"
            )

        elif last.result in {
            "win",
            "walkover_win",
        }:
            heading = "勝ち上がり"

        else:
            heading = "結果"

        individual_sections.append(
            {
                "player": player_name,
                "heading": heading,
                "entries": entries,
            }
        )


    def section_sort_key(
        section: dict,
    ) -> tuple:
        player = player_map.get(
            section["player"]
        )

        if player is None:
            return (
                1,
                0,
                section["player"],
            )

        is_captain = (
            "主将" in (player.note or "")
        )

        return (
            0 if is_captain else 1,
            -player_grade_number(player),
            player.name,
        )

    individual_sections.sort(
        key=section_sort_key
    )

    return {
        "individual_sections": individual_sections,
        "team_events": team_events,
    }


@app.exception_handler(401)
async def unauthorized_handler(request: Request, exc: HTTPException):
    return RedirectResponse("/login", status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return render("login.html", request=request, error="")


@app.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username.strip()))
    if not user or not verify_password(password, user.password_hash):
        return render(
            "login.html",
            request=request,
            error="ユーザー名またはパスワードが違います。",
        )
    request.session["user_id"] = user.id
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def dashboard(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    tournaments = db.scalars(
        select(Tournament).order_by(Tournament.event_date.desc(), Tournament.id.desc())
    ).all()
    return render("index.html", request=request, user=user, tournaments=tournaments)
# =========================
# 一般公開用 試合結果一覧
# =========================

@app.get("/results", response_class=HTMLResponse)
def public_results(
    request: Request,
    db: Session = Depends(get_db),
):
    tournaments = db.scalars(
        select(Tournament).order_by(
            Tournament.event_date.desc(),
            Tournament.id.desc(),
        )
    ).all()

    return render(
        "public_results.html",
        request=request,
        tournaments=tournaments,
    )


# =========================
# 一般公開用 大会詳細
# =========================

@app.get(
    "/results/tournaments/{tournament_id}",
    response_class=HTMLResponse,
)
def public_tournament_detail(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    tournament = db.get(
        Tournament,
        tournament_id,
    )

    if not tournament:
        raise HTTPException(status_code=404)

    view = tournament_view(
        tournament,
        db,
    )

    return render(
        "public_tournament_detail.html",
        request=request,
        tournament=tournament,
        **view,
    )

#選手登録
@app.get("/players", response_class=HTMLResponse)
def players_page(
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    delete_graduated_players(db)

    players = get_ordered_players(db)

    

    return render(
        "players.html",
        request=request,
        user=user,
        players=players,
    )



@app.post("/players")
def create_player(
    request: Request,
    name: str = Form(...),
    enrollment_year: int = Form(...),
    note: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    cleaned_name = name.strip()

    if (
        cleaned_name
        and not db.scalar(
            select(Player).where(
                Player.name == cleaned_name
            )
        )
    ):
        db.add(
            Player(
                name=cleaned_name,
                enrollment_year=enrollment_year,
                grade="",
                note=note.strip(),
            )
        )

        db.commit()

    return RedirectResponse(
        "/players",
        status_code=303,
    )


@app.post("/players/{player_id}/delete")
def delete_player(
    player_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    player = db.get(Player, player_id)
    if player:
        db.delete(player)
        db.commit()
    return RedirectResponse("/players", status_code=303)


@app.get("/users", response_class=HTMLResponse)
def users_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    if user.role != "admin":
        return RedirectResponse("/", status_code=303)
    users = db.scalars(select(User).order_by(User.username)).all()
    return render("users.html", request=request, user=user, users=users)


@app.post("/users")
def create_user(
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form("member"),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    if user.role != "admin":
        raise HTTPException(status_code=403)
    username = username.strip()
    if (
        username
        and password
        and not db.scalar(select(User).where(User.username == username))
    ):
        db.add(
            User(username=username, password_hash=hash_password(password), role=role)
        )
        db.commit()
    return RedirectResponse("/users", status_code=303)


@app.get("/tournaments/new", response_class=HTMLResponse)
def new_tournament_page(request: Request, user: User = Depends(require_user)):
    return render(
        "tournament_form.html",
        request=request,
        user=user,
        today=date.today().isoformat(),
    )


@app.post("/tournaments")
def create_tournament(
    name: str = Form(...),
    event_date: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tournament = Tournament(
        name=name.strip(),
        event_date=date.fromisoformat(event_date),
        venue="",
        created_by=user.id,
    )

    db.add(tournament)
    db.commit()
    db.refresh(tournament)

    return RedirectResponse(
        f"/tournaments/{tournament.id}",
        status_code=303,
    )


@app.get(
    "/tournaments/{tournament_id}",
    response_class=HTMLResponse,
)
def tournament_detail(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tournament = db.get(
        Tournament,
        tournament_id,
    )

    if not tournament:
        raise HTTPException(
            status_code=404
        )

    view = tournament_view(
        tournament,
        db,
    )

    return render(
        "tournament_detail.html",
        request=request,
        user=user,
        tournament=tournament,
        **view,
    )

@app.get(
    "/tournaments/{tournament_id}/edit",
    response_class=HTMLResponse,
)
def edit_tournament_page(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tournament = db.get(Tournament, tournament_id)

    if not tournament:
        raise HTTPException(status_code=404)

    return render(
        "tournament_edit.html",
        request=request,
        user=user,
        tournament=tournament,
    )


@app.post("/tournaments/{tournament_id}/edit")
async def update_tournament(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tournament = db.get(Tournament, tournament_id)

    if not tournament:
        raise HTTPException(status_code=404)

    form = await request.form()

    name = str(
        form.get("name", "")
    ).strip()

    event_date_text = str(
        form.get("event_date", "")
    ).strip()

    if not name:
        raise HTTPException(
            status_code=400,
            detail="大会名を入力してください。",
        )

    if not event_date_text:
        raise HTTPException(
            status_code=400,
            detail="開催日を入力してください。",
        )

    try:
        event_date_value = date.fromisoformat(
            event_date_text
        )
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="開催日の形式が正しくありません。",
        )

    tournament.name = name
    tournament.event_date = event_date_value

    db.commit()
    db.refresh(tournament)

    return RedirectResponse(
        f"/tournaments/{tournament.id}",
        status_code=303,
    )

@app.get("/tournaments/{tournament_id}/results/new", response_class=HTMLResponse)
def new_result_page(
    tournament_id: int,
    request: Request,
    event_type: str = "team",
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tournament = db.get(Tournament, tournament_id)
    if not tournament:
        raise HTTPException(status_code=404)
    event_type = "individual" if event_type == "individual" else "team"
    players = get_ordered_players(db)
    return render(
        "event_form.html",
        request=request,
        user=user,
        players=players,
        event_type=event_type,
        tournament=tournament,
    )


@app.post("/tournaments/{tournament_id}/results")
async def create_result(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tournament = db.get(Tournament, tournament_id)

    if not tournament:
        raise HTTPException(status_code=404)

    form = await request.form()

    event_type = "individual" if form.get("event_type") == "individual" else "team"

    team_round_raw = str(form.get("team_round", "1")).strip()

    event = Event(
        tournament_id=tournament.id,
        event_type=event_type,
        round_name=str(form.get("round_name", "")).strip(),
        team_round=int(team_round_raw or 1),
        opponent_team=str(form.get("opponent_team", "")).strip(),
        created_by=user.id,
    )

    db.add(event)
    db.flush()

    fields = {
        name: form.getlist(name)
        for name in [
            "player_name",
            "opponent_name",
            "result",
            "score_for",
            "score_against",
            "detail",
            "position",
            "individual_round",
        ]
    }

    for index, raw_player_name in enumerate(fields["player_name"]):
        player_name = str(raw_player_name).strip()

        if not player_name:
            continue

        def item(
            name: str,
            default: str = "",
        ) -> str:
            if index < len(fields[name]):
                return str(fields[name][index]).strip()

            return default

        position = item("position")

        db.add(
            MatchEntry(
                event_id=event.id,
                order_index=(
                    team_entry_order(position, index)
                    if event_type == "team"
                    else index
                ),
                position=position,
                player_name=player_name,
                opponent_name=item("opponent_name"),
                individual_round=item("individual_round"),
                result=item(
                    "result",
                    "draw",
                ),
                score_for=int(item("score_for", "0") or 0),
                score_against=int(
                    item(
                        "score_against",
                        "0",
                    )
                    or 0
                ),
                detail=item("detail"),
            )
        )

    db.commit()

    return RedirectResponse(
        f"/tournaments/{tournament.id}",
        status_code=303,
    )


@app.get("/events/{event_id}/edit", response_class=HTMLResponse)
def edit_event_page(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    event = db.get(Event, event_id)

    if not event:
        raise HTTPException(status_code=404)

    players = get_ordered_players(db)

    return render(
        "event_edit.html",
        request=request,
        user=user,
        event=event,
        tournament=event.tournament_parent,
        players=players,
    )

@app.get(
    "/tournaments/{tournament_id}/individual-results/edit",
    response_class=HTMLResponse,
)
def edit_individual_results_page(
    tournament_id: int,
    player_name: str,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tournament = db.get(Tournament, tournament_id)

    if not tournament:
        raise HTTPException(status_code=404)

    entries = db.scalars(
        select(MatchEntry)
        .join(Event)
        .where(
            Event.tournament_id == tournament_id,
            Event.event_type == "individual",
            MatchEntry.player_name == player_name,
        )
        .order_by(
            MatchEntry.individual_round,
            MatchEntry.order_index,
        )
    ).all()

    print("個人戦の取得件数:", len(entries))

    for entry in entries:
        print(
            entry.id,
            entry.player_name,
            entry.individual_round,
            entry.opponent_name,
        )

    if not entries:
        raise HTTPException(status_code=404)

    players = get_ordered_players(db)

    return render(
        "individual_edit.html",
        request=request,
        user=user,
        tournament=tournament,
        player_name=player_name,
        entries=entries,
        players=players,
    )

@app.post(
    "/tournaments/{tournament_id}/individual-results/edit"
)
async def update_individual_results(
    tournament_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tournament = db.get(Tournament, tournament_id)

    if not tournament:
        raise HTTPException(status_code=404)

    form = await request.form()

    original_player_name = str(
        form.get("original_player_name", "")
    ).strip()

    if not original_player_name:
        raise HTTPException(status_code=400)

    old_entries = db.scalars(
        select(MatchEntry)
        .join(Event)
        .where(
            Event.tournament_id == tournament_id,
            Event.event_type == "individual",
            MatchEntry.player_name == original_player_name,
        )
    ).all()

    related_events = {
        entry.event
        for entry in old_entries
    }

    for entry in old_entries:
        db.delete(entry)

    db.flush()

    for event in related_events:
        db.refresh(event)

        if not event.entries:
            db.delete(event)

    db.flush()

    event = Event(
        tournament_id=tournament.id,
        event_type="individual",
        round_name="",
        team_round=1,
        opponent_team="",
        created_by=user.id,
    )

    db.add(event)
    db.flush()

    field_names = [
        "player_name",
        "opponent_name",
        "result",
        "score_for",
        "score_against",
        "detail",
        "individual_round",
    ]

    fields = {
        name: form.getlist(name)
        for name in field_names
    }

    for index, raw_player_name in enumerate(
        fields["player_name"]
    ):
        def item(
            name: str,
            default: str = "",
        ) -> str:
            if index < len(fields[name]):
                return str(
                    fields[name][index]
                ).strip()

            return default

        player_name = str(
            raw_player_name
        ).strip()

        opponent_name = item(
            "opponent_name"
        )

        if not player_name:
            continue

        db.add(
            MatchEntry(
                event_id=event.id,
                order_index=index,
                position="",
                player_name=player_name,
                opponent_name=opponent_name,
                individual_round=item(
                    "individual_round"
                ),
                result=item(
                    "result",
                    "draw",
                ),
                score_for=int(
                    item(
                        "score_for",
                        "0",
                    ) or 0
                ),
                score_against=int(
                    item(
                        "score_against",
                        "0",
                    ) or 0
                ),
                detail=item("detail"),
            )
        )


    db.commit()

    return RedirectResponse(
        f"/tournaments/{tournament.id}",
        status_code=303,
    )

@app.post("/events/{event_id}/edit")
async def update_event(
    event_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    event = db.get(Event, event_id)

    if not event:
        raise HTTPException(status_code=404)

    form = await request.form()

    event.round_name = str(form.get("round_name", "")).strip()
    event.opponent_team = str(form.get("opponent_team", "")).strip()

    if event.event_type == "team":
        event.team_round = int(str(form.get("team_round", "1")) or 1)

    for entry in list(event.entries):
        db.delete(entry)

    db.flush()

    fields = {
        name: form.getlist(name)
        for name in [
            "player_name",
            "opponent_name",
            "result",
            "score_for",
            "score_against",
            "detail",
            "position",
            "individual_round",
        ]
    }

    for index, raw_player_name in enumerate(fields["player_name"]):
        player_name = str(raw_player_name).strip()

        if not player_name:
            continue

        def item(name: str, default: str = "") -> str:
            if index < len(fields[name]):
                return str(fields[name][index]).strip()
            return default

        position = item("position")

        db.add(
            MatchEntry(
                event_id=event.id,
                order_index=(
                    team_entry_order(position, index)
                    if event.event_type == "team"
                    else index
                ),
                position=position,
                player_name=player_name,
                opponent_name=item("opponent_name"),
                individual_round=item("individual_round"),
                result=item("result", "draw"),
                score_for=int(item("score_for", "0") or 0),
                score_against=int(item("score_against", "0") or 0),
                detail=item("detail"),
            )
        )

    db.commit()

    return RedirectResponse(
        f"/tournaments/{event.tournament_id}",
        status_code=303,
    )


@app.post("/events/{event_id}/delete")
def delete_event(
    event_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)
):
    event = db.get(Event, event_id)
    tournament_id = event.tournament_id if event else None
    if event:
        db.delete(event)
        db.commit()
    return RedirectResponse(
        f"/tournaments/{tournament_id}" if tournament_id else "/", status_code=303
    )


@app.post("/tournaments/{tournament_id}/delete")
def delete_tournament(
    tournament_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    tournament = db.get(Tournament, tournament_id)
    if tournament:
        db.delete(tournament)
        db.commit()
    return RedirectResponse("/", status_code=303)