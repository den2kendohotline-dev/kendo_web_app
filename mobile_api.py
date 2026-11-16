import hashlib
import secrets
from datetime import datetime, timedelta, date

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, select, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column, selectinload

from main import (
    Base,
    engine,
    get_db,
    User,
    Tournament,
    Player,
    Event,
    MatchEntry,
    VideoPlaylist,
    verify_password,
)

class MobileToken(Base):
    __tablename__ = "mobile_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

class IndividualAward(Base):
    __tablename__ = 'mobile_individual_awards'
    __table_args__ = (UniqueConstraint('tournament_id', 'player_name', name='uq_mobile_individual_award'),)

    id: Mapped[int] = mapped_column(primary_key=True)
    tournament_id: Mapped[int] = mapped_column(ForeignKey('tournaments.id'), index=True)
    player_name: Mapped[str] = mapped_column(String(100))
    award: Mapped[str] = mapped_column(String(30))

class TeamAward(Base):
    __tablename__ = "mobile_team_awards"

    tournament_id: Mapped[int] = mapped_column(
        ForeignKey("tournaments.id"),
        primary_key=True,
    )
    award: Mapped[str] = mapped_column(String(30))


Base.metadata.create_all(
    bind=engine,
    tables=[
        MobileToken.__table__,
        IndividualAward.__table__,
        TeamAward.__table__,
        VideoPlaylist.__table__,
    ],
)
router = APIRouter()

class LoginIn(BaseModel):
    username: str
    password: str


def mobile_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="ログインが必要です")
    digest = hashlib.sha256(token.encode()).hexdigest()
    record = db.scalar(select(MobileToken).where(MobileToken.token_hash == digest))
    if not record or record.expires_at <= datetime.utcnow():
        raise HTTPException(status_code=401, detail="ログインの有効期限が切れました")
    user = db.get(User, record.user_id)
    if not user:
        raise HTTPException(status_code=401, detail="ユーザーが見つかりません")
    return user


@router.post("/api/v1/auth/login")
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username.strip()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="ユーザー名またはパスワードが違います")
    raw = secrets.token_urlsafe(48)
    db.add(MobileToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=datetime.utcnow() + timedelta(days=14),
    ))
    db.commit()
    return {"access_token": raw, "token_type": "bearer", "user": {
        "id": user.id, "username": user.username, "role": user.role,
    }}


@router.post("/api/v1/auth/logout", status_code=204)
def logout(
    authorization: str | None = Header(default=None),
    user: User = Depends(mobile_user),
    db: Session = Depends(get_db),
):
    token = authorization.partition(" ")[2]
    digest = hashlib.sha256(token.encode()).hexdigest()
    record = db.scalar(select(MobileToken).where(MobileToken.token_hash == digest))
    if record:
        db.delete(record)
        db.commit()


@router.get("/api/v1/auth/me")
def me(user: User = Depends(mobile_user)):
    return {"id": user.id, "username": user.username, "role": user.role}


def team_entry_order(position, order_index):
    """Keep stored entry order when displaying team matches."""
    return order_index if order_index is not None else 0


def current_grade(player):
    from main import current_grade as site_current_grade
    return site_current_grade(player)

def entry_data(m: MatchEntry):
    return {
        "id": m.id, "position": m.position, "player_name": m.player_name,
        "opponent_name": m.opponent_name, "individual_round": m.individual_round,
        "result": m.result, "score_for": m.score_for,
        "score_against": m.score_against, "detail": m.detail,
    }


def event_data(e: Event, *, full: bool = False):
    t = e.tournament_parent
    result = {
        "id": e.id, "tournament_id": t.id, "tournament": t.name,
        "event_date": t.event_date.isoformat(), "venue": t.venue,
        "event_type": e.event_type, "round_name": e.round_name,
        "team_round": e.team_round, "opponent_team": e.opponent_team,
        "created_by": e.created_by,
    }
    if full:
        entries = sorted(e.entries, key=lambda m: (
            team_entry_order(m.position, m.order_index)
            if e.event_type == "team" else m.order_index
        ))
        result["entries"] = [entry_data(m) for m in entries]
    return result


def event_query():
    return select(Event).options(
        selectinload(Event.tournament_parent), selectinload(Event.entries)
    ).order_by(Event.id.desc())


@router.get("/api/v1/events")
def private_events(user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    events = db.scalars(event_query()).all()
    events = sorted(events, key=lambda e: (e.tournament_parent.event_date, e.id), reverse=True)
    return [event_data(e) for e in events]


@router.get("/api/v1/events/{event_id}")
def private_event(event_id: int, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    e = db.scalar(event_query().where(Event.id == event_id))
    if not e:
        raise HTTPException(status_code=404, detail="試合が見つかりません")
    return event_data(e, full=True)


@router.get("/api/v1/players")
def private_players(user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    from main import get_ordered_players

    return [
        {
            "id": p.id,
            "name": p.name,
            "grade": current_grade(p),
            "enrollment_year": p.enrollment_year,
            "note": p.note,
        }
        for p in get_ordered_players(db)
    ]

@router.get("/api/public/tournaments")
def public_tournaments(db: Session = Depends(get_db)):
    tournaments = db.scalars(select(Tournament).order_by(
        Tournament.event_date.desc(), Tournament.id.desc()
    )).all()
    return [{"id": t.id, "name": t.name,
             "event_date": t.event_date.isoformat(), "venue": t.venue}
            for t in tournaments]


@router.get('/api/v1/tournaments/{tournament_id}/individual-awards')
def get_individual_awards(tournament_id: int, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    if db.get(Tournament, tournament_id) is None:
        raise HTTPException(404, '大会が見つかりません。')
    awards = db.scalars(select(IndividualAward).where(IndividualAward.tournament_id == tournament_id)).all()
    return {a.player_name: a.award for a in awards}


class IndividualAwardUpdate(BaseModel):
    player_name: str = Field(min_length=1, max_length=100)
    award: str = ''


@router.put('/api/v1/tournaments/{tournament_id}/individual-awards')
def put_individual_award(tournament_id: int, payload: IndividualAwardUpdate, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    if db.get(Tournament, tournament_id) is None:
        raise HTTPException(404, '大会が見つかりません。')
    if payload.award not in {'', '優勝', '準優勝', '第3位', 'ベスト8'}:
        raise HTTPException(422, '順位が正しくありません。')
    name = payload.player_name.strip()
    if not name:
        raise HTTPException(422, '選手名を入力してください。')
    existing = db.scalar(select(IndividualAward).where(
        IndividualAward.tournament_id == tournament_id,
        IndividualAward.player_name == name,
    ))
    if payload.award:
        if existing:
            existing.award = payload.award
        else:
            db.add(IndividualAward(tournament_id=tournament_id, player_name=name, award=payload.award))
    elif existing:
        db.delete(existing)
    db.commit()
    return {'player_name': name, 'award': payload.award}

@router.get("/api/v1/tournaments/{tournament_id}/team-award")
def get_team_award(
    tournament_id: int,
    user: User = Depends(mobile_user),
    db: Session = Depends(get_db),
):
    if db.get(Tournament, tournament_id) is None:
        raise HTTPException(404, "大会が見つかりません。")

    award = db.get(TeamAward, tournament_id)

    return {"award": award.award if award else ""}


class TeamAwardUpdate(BaseModel):
    award: str = ""


@router.put("/api/v1/tournaments/{tournament_id}/team-award")
def put_team_award(
    tournament_id: int,
    payload: TeamAwardUpdate,
    user: User = Depends(mobile_user),
    db: Session = Depends(get_db),
):
    if db.get(Tournament, tournament_id) is None:
        raise HTTPException(404, "大会が見つかりません。")

    allowed = {"", "優勝", "準優勝", "第3位", "ベスト8"}

    if payload.award not in allowed:
        raise HTTPException(422, "順位が正しくありません。")

    existing = db.get(TeamAward, tournament_id)

    if payload.award:
        if existing:
            existing.award = payload.award
        else:
            db.add(TeamAward(
                tournament_id=tournament_id,
                award=payload.award,
            ))
    elif existing:
        db.delete(existing)

    db.commit()

    return {"award": payload.award}

@router.get("/api/public/tournaments/{tournament_id}")
def public_tournament(tournament_id: int, db: Session = Depends(get_db)):
    t = db.get(Tournament, tournament_id)

    if t is None:
        raise HTTPException(status_code=404, detail="大会が見つかりません")
    events = db.scalars(
        select(Event).options(
            selectinload(Event.tournament_parent), selectinload(Event.entries)
        ).where(Event.tournament_id == tournament_id).order_by(Event.id)
    ).all()
    return {
        "id": t.id,
        "name": t.name,
        "event_date": t.event_date.isoformat(),
        "venue": t.venue,
        "events": [event_data(e, full=True) for e in events],
    }

# Mobile client write endpoints
from pydantic import Field
from sqlalchemy.exc import IntegrityError
from main import current_grade as site_current_grade, team_entry_order as site_team_entry_order

class PlayerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enrollment_year: int = Field(ge=2000, le=2100)
    grade: str = ""
    note: str = ""

@router.post('/api/v1/players', status_code=201)
def create_mobile_player(payload: PlayerCreate, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, '選手名を入力してください。')
    if db.scalar(select(Player).where(Player.name == name)):
        raise HTTPException(409, '同じ名前の選手が登録されています。')
    enrollment_year = payload.enrollment_year
    if enrollment_year is None and payload.grade:
        try:
            n = int(payload.grade.replace('年', '').strip())
            if 1 <= n <= 4:
                from main import current_academic_year
                enrollment_year = current_academic_year() - n + 1
        except ValueError:
            pass
    p = Player(name=name, enrollment_year=enrollment_year, grade=payload.grade, note=payload.note.strip())
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '同じ名前の選手が登録されています。')
    db.refresh(p)
    return {'id': p.id, 'name': p.name, 'grade': site_current_grade(p), 'enrollment_year': p.enrollment_year, 'note': p.note}

@router.delete('/api/v1/players/{player_id}', status_code=204)
def delete_mobile_player(player_id: int, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    p = db.get(Player, player_id)
    if p is None:
        raise HTTPException(404, '選手が見つかりません。')
    db.delete(p)
    db.commit()

class EntryCreate(BaseModel):
    position: str = ''
    player_name: str = ''
    opponent_name: str = ''
    individual_round: str = ''
    result: str = 'draw'
    score_for: int = 0
    score_against: int = 0
    detail: str = ''

class EventCreate(BaseModel):
    event_type: str
    tournament: str = ''
    tournament_id: int | None = None
    event_date: date | None = None
    venue: str = ''
    round_name: str = ''
    team_round: int = 1
    opponent_team: str = ''
    entries: list[EntryCreate] = Field(default_factory=list)

class TournamentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    event_date: date
    venue: str = ''


@router.post('/api/v1/tournaments', status_code=201)
def create_mobile_tournament(payload: TournamentCreate, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, '大会名を入力してください。')
    existing = db.scalar(select(Tournament).where(Tournament.name == name, Tournament.event_date == payload.event_date))
    if existing:
        raise HTTPException(409, '同じ大会名・開催日の大会が既に登録されています。')
    t = Tournament(name=name, event_date=payload.event_date, venue=payload.venue.strip(), created_by=user.id)
    db.add(t)
    db.commit()
    db.refresh(t)
    return {'id': t.id, 'name': t.name, 'event_date': t.event_date.isoformat(), 'venue': t.venue}


@router.put('/api/v1/tournaments/{tournament_id}')
def update_mobile_tournament(tournament_id: int, payload: TournamentCreate, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    t = db.get(Tournament, tournament_id)
    if t is None:
        raise HTTPException(404, '大会が見つかりません。')
    name = payload.name.strip()
    if not name:
        raise HTTPException(422, '大会名を入力してください。')
    duplicate = db.scalar(select(Tournament).where(Tournament.name == name, Tournament.event_date == payload.event_date, Tournament.id != tournament_id))
    if duplicate:
        raise HTTPException(409, '同じ大会名・開催日の大会が既に登録されています。')
    t.name = name
    t.event_date = payload.event_date
    t.venue = payload.venue.strip()
    db.commit()
    return {'id': t.id, 'name': t.name, 'event_date': t.event_date.isoformat(), 'venue': t.venue}

@router.delete('/api/v1/tournaments/{tournament_id}', status_code=204)
def delete_mobile_tournament(
    tournament_id: int,
    user: User = Depends(mobile_user),
    db: Session = Depends(get_db),
):
    t = db.get(Tournament, tournament_id)
    if t is None:
        raise HTTPException(404, '大会が見つかりません。')

    db.delete(t)
    db.commit()

@router.post('/api/v1/events', status_code=201)
def create_mobile_event(payload: EventCreate, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    if payload.event_type not in {'team', 'individual'}:
        raise HTTPException(422, '試合区分が正しくありません。')
    if not payload.entries or not any(x.player_name.strip() for x in payload.entries):
        raise HTTPException(422, '選手を1人以上入力してください。')
    if payload.tournament_id is not None:
        t = db.get(Tournament, payload.tournament_id)
        if t is None:
            raise HTTPException(404, '大会が見つかりません。')
    else:
        if not payload.tournament.strip() or payload.event_date is None:
            raise HTTPException(422, '大会名と開催日を入力してください。')
        t = db.scalar(select(Tournament).where(Tournament.name == payload.tournament.strip(), Tournament.event_date == payload.event_date))
        if t is None:
            t = Tournament(name=payload.tournament.strip(), event_date=payload.event_date, venue=payload.venue.strip(), created_by=user.id)
            db.add(t)
            db.flush()
    e = Event(tournament_id=t.id, event_type=payload.event_type, round_name=payload.round_name.strip(), team_round=payload.team_round, opponent_team=payload.opponent_team.strip(), created_by=user.id)
    db.add(e)
    db.flush()
    for i, item in enumerate(payload.entries):
        if not item.player_name.strip():
            if payload.event_type != 'team' or item.result != 'walkover_loss':
                continue
        db.add(MatchEntry(event_id=e.id, order_index=site_team_entry_order(item.position, i) if payload.event_type == 'team' else i, position=item.position, player_name=item.player_name.strip(), opponent_name=item.opponent_name.strip(), individual_round=item.individual_round, result=item.result, score_for=item.score_for, score_against=item.score_against, detail=item.detail))
    db.commit()
    return {'id': e.id, 'tournament_id': t.id}

@router.delete('/api/v1/events/{event_id}', status_code=204)
def delete_mobile_event(event_id: int, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    e = db.get(Event, event_id)
    if e is None:
        raise HTTPException(404, '試合が見つかりません。')
    db.delete(e)
    db.commit()


@router.put('/api/v1/events/{event_id}')
def update_mobile_event(event_id: int, payload: EventCreate, user: User = Depends(mobile_user), db: Session = Depends(get_db)):
    e = db.scalar(event_query().where(Event.id == event_id))
    if e is None:
        raise HTTPException(404, '試合が見つかりません。')
    if payload.event_type != e.event_type:
        raise HTTPException(422, '団体戦と個人戦の区分は変更できません。')
    if not payload.tournament.strip() or payload.event_date is None:
        raise HTTPException(422, '大会名と開催日を入力してください。')
    if not payload.entries or any(
    not x.player_name.strip()
    and not (
        payload.event_type == 'team'
        and x.result == 'walkover_loss'
    )
    for x in payload.entries
):
        raise HTTPException(422, 'すべての試合で選手を選択してください。')
    if payload.event_type == 'team' and not payload.opponent_team.strip():
        raise HTTPException(422, '対戦校・団体名を入力してください。')

    t = e.tournament_parent
    e.round_name = payload.round_name.strip()
    e.team_round = payload.team_round
    e.opponent_team = payload.opponent_team.strip() if e.event_type == 'team' else ''

    for old in list(e.entries):
        db.delete(old)
    db.flush()
    for i, item in enumerate(payload.entries):
        db.add(MatchEntry(
            event_id=e.id,
            order_index=site_team_entry_order(item.position, i) if e.event_type == 'team' else i,
            position=item.position.strip(),
            player_name=item.player_name.strip(),
            opponent_name=item.opponent_name.strip(),
            individual_round=item.individual_round.strip(),
            result=item.result,
            score_for=item.score_for,
            score_against=item.score_against,
            detail=item.detail.strip(),
        ))
    db.commit()
    return {'id': e.id, 'tournament_id': t.id}


class VideoPlaylistIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    category: str
    youtube_url: str = Field(min_length=1, max_length=500)
    playlist_date: date

def validate_video_playlist(payload: VideoPlaylistIn):
    from urllib.parse import urlparse, parse_qs

    title = payload.title.strip()
    category = payload.category.strip()
    url = payload.youtube_url.strip()

    if not title:
        raise HTTPException(422, "タイトルを入力してください。")

    if category not in {"match", "practice"}:
        raise HTTPException(422, "カテゴリが正しくありません。")

    parsed = urlparse(url)

    if (
        parsed.scheme != "https"
        or parsed.hostname not in {"www.youtube.com", "youtube.com", "m.youtube.com"}
        or parsed.path != "/playlist"
    ):
        raise HTTPException(422, "YouTube再生リストのURLを入力してください。")

    playlist_id = parse_qs(parsed.query).get("list", [""])[0]

    if not playlist_id or not all(c.isalnum() or c in "_-" for c in playlist_id):
        raise HTTPException(422, "再生リストのURLが正しくありません。")

    return title, category, f"https://www.youtube.com/playlist?list={playlist_id}"


def video_playlist_data(playlist: VideoPlaylist):
    return {
        "id": playlist.id,
        "title": playlist.title,
        "category": playlist.category,
        "youtube_url": playlist.youtube_url,
        "playlist_date": playlist.playlist_date.isoformat() if playlist.playlist_date else None,
    }


@router.get("/api/v1/video-playlists")
def get_video_playlists(
    user: User = Depends(mobile_user),
    db: Session = Depends(get_db),
):
    playlists = db.scalars(
        select(VideoPlaylist).order_by(
            VideoPlaylist.playlist_date.desc(),
            VideoPlaylist.id.desc()
      )
    ).all()

    return [video_playlist_data(p) for p in playlists]


@router.post("/api/v1/video-playlists", status_code=201)
def create_video_playlist(
    payload: VideoPlaylistIn,
    user: User = Depends(mobile_user),
    db: Session = Depends(get_db),
):
    title, category, url = validate_video_playlist(payload)

    playlist = VideoPlaylist(
        title=title,
        category=category,
        youtube_url=url,
        playlist_date=payload.playlist_date,
    )

    db.add(playlist)
    db.commit()
    db.refresh(playlist)

    return video_playlist_data(playlist)


@router.put("/api/v1/video-playlists/{playlist_id}")
def update_video_playlist(
    playlist_id: int,
    payload: VideoPlaylistIn,
    user: User = Depends(mobile_user),
    db: Session = Depends(get_db),
):
    playlist = db.get(VideoPlaylist, playlist_id)

    if playlist is None:
        raise HTTPException(404, "再生リストが見つかりません。")

    title, category, url = validate_video_playlist(payload)

    playlist.title = title
    playlist.category = category
    playlist.youtube_url = url
    playlist.playlist_date = payload.playlist_date

    db.commit()
    db.refresh(playlist)

    return video_playlist_data(playlist)


@router.delete("/api/v1/video-playlists/{playlist_id}", status_code=204)
def delete_video_playlist(
    playlist_id: int,
    user: User = Depends(mobile_user),
    db: Session = Depends(get_db),
):
    playlist = db.get(VideoPlaylist, playlist_id)

    if playlist is None:
        raise HTTPException(404, "再生リストが見つかりません。")

    db.delete(playlist)
    db.commit()
