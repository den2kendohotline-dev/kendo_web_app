"""JSON API for the separately installed mobile client. Existing HTML routes stay intact."""
import hashlib
import secrets
from datetime import date, datetime, timedelta
from typing import Literal
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, String, select, delete
from sqlalchemy.orm import Mapped, Session, mapped_column
from main import Base, engine, get_db, User, Player, Event, MatchEntry, verify_password, hash_password, build_report

class MobileToken(Base):
    __tablename__ = 'mobile_tokens'
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id'), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)

Base.metadata.create_all(bind=engine, tables=[MobileToken.__table__])
router = APIRouter(prefix='/api/v1', tags=['mobile'])

def require_mobile(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.startswith('Bearer '):
        raise HTTPException(401, 'ログインが必要です')
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    token = db.scalar(select(MobileToken).where(MobileToken.token_hash == digest))
    if not token or token.expires_at <= datetime.utcnow():
        raise HTTPException(401, 'ログイン期限が切れました')
    user = db.get(User, token.user_id)
    if not user:
        raise HTTPException(401, 'ユーザーが見つかりません')
    return user

class LoginIn(BaseModel):
    username: str
    password: str

@router.post('/auth/login')
def api_login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.username == payload.username.strip()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, 'ユーザー名またはパスワードが違います')
    raw = secrets.token_urlsafe(48)
    db.add(MobileToken(user_id=user.id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=datetime.utcnow()+timedelta(days=14)))
    db.commit()
    return {'access_token': raw, 'token_type': 'bearer', 'user': {'id': user.id, 'username': user.username, 'role': user.role}}

@router.post('/auth/logout', status_code=204)
def api_logout(authorization: str | None = Header(default=None), user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    digest = hashlib.sha256(authorization[7:].encode()).hexdigest()
    token = db.scalar(select(MobileToken).where(MobileToken.token_hash == digest))
    if token:
        db.delete(token)
        db.commit()

@router.get('/auth/me')
def api_me(user: User = Depends(require_mobile)):
    return {'id': user.id, 'username': user.username, 'role': user.role}

class PlayerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    grade: str = Field(default='', max_length=50)
    note: str = Field(default='', max_length=255)

@router.get('/players')
def api_players(user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    return [{'id': p.id, 'name': p.name, 'grade': p.grade, 'note': p.note} for p in db.scalars(select(Player).order_by(Player.name))]

@router.post('/players', status_code=201)
def api_add_player(payload: PlayerIn, user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    name = payload.name.strip()
    if not name: raise HTTPException(422, '名前を入力してください')
    if db.scalar(select(Player).where(Player.name == name)):
        raise HTTPException(409, 'すでに登録されています')
    p = Player(name=name, grade=payload.grade.strip(), note=payload.note.strip())
    db.add(p); db.commit(); db.refresh(p)
    return {'id': p.id, 'name': p.name}

@router.delete('/players/{player_id}', status_code=204)
def api_delete_player(player_id: int, user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    p = db.get(Player, player_id)
    if not p: raise HTTPException(404, '選手が見つかりません')
    db.delete(p); db.commit()

class EntryIn(BaseModel):
    position: str = Field(default='', max_length=50)
    player_name: str = Field(min_length=1, max_length=100)
    opponent_name: str = Field(default='', max_length=150)
    individual_round: str = Field(default='', max_length=80)
    result: Literal['win', 'loss', 'draw'] = 'draw'
    score_for: int = Field(default=0, ge=0, le=2)
    score_against: int = Field(default=0, ge=0, le=2)
    detail: str = Field(default='', max_length=255)

class EventIn(BaseModel):
    event_type: Literal['team', 'individual']
    tournament: str = Field(min_length=1, max_length=200)
    event_date: date
    venue: str = Field(default='', max_length=200)
    round_name: str = Field(default='', max_length=100)
    opponent_team: str = Field(default='', max_length=200)
    entries: list[EntryIn] = Field(min_length=1, max_length=100)

def serialize_event(e: Event, full=False):
    data = {'id': e.id, 'event_type': e.event_type, 'tournament': e.tournament, 'event_date': e.event_date.isoformat(), 'venue': e.venue, 'round_name': e.round_name, 'opponent_team': e.opponent_team, 'created_by': e.created_by}
    if full:
        data['entries'] = [{'id': m.id, 'position': m.position, 'player_name': m.player_name, 'opponent_name': m.opponent_name, 'individual_round': m.individual_round, 'result': m.result, 'score_for': m.score_for, 'score_against': m.score_against, 'detail': m.detail} for m in e.entries]
        data['report'] = build_report(e)
    return data

@router.get('/events')
def api_events(user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    return [serialize_event(e) for e in db.scalars(select(Event).order_by(Event.event_date.desc(), Event.id.desc())).all()]

@router.post('/events', status_code=201)
def api_add_event(payload: EventIn, user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    if not payload.tournament.strip(): raise HTTPException(422, '大会名を入力してください')
    if payload.event_type == 'team' and not payload.opponent_team.strip(): raise HTTPException(422, '対戦団体名を入力してください')
    if any(not x.player_name.strip() for x in payload.entries): raise HTTPException(422, '選手名を入力してください')
    event = Event(event_type=payload.event_type, tournament=payload.tournament.strip(), event_date=payload.event_date, venue=payload.venue.strip(), round_name=payload.round_name.strip(), opponent_team=payload.opponent_team.strip(), created_by=user.id)
    db.add(event); db.flush()
    for i, item in enumerate(payload.entries):
        db.add(MatchEntry(event_id=event.id, order_index=i, **item.model_dump()))
    db.commit(); db.refresh(event)
    return {'id': event.id}

@router.get('/events/{event_id}')
def api_event(event_id: int, user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if not event: raise HTTPException(404, '大会が見つかりません')
    return serialize_event(event, full=True)

@router.delete('/events/{event_id}', status_code=204)
def api_delete_event(event_id: int, user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    event = db.get(Event, event_id)
    if not event: raise HTTPException(404, '大会が見つかりません')
    db.delete(event); db.commit()

class UserIn(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=8)
    role: Literal['admin', 'member'] = 'member'

@router.get('/users')
def api_users(user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    if user.role != 'admin': raise HTTPException(403)
    return [{'id': u.id, 'username': u.username, 'role': u.role} for u in db.scalars(select(User).order_by(User.username))]

@router.post('/users', status_code=201)
def api_add_user(payload: UserIn, user: User = Depends(require_mobile), db: Session = Depends(get_db)):
    if user.role != 'admin': raise HTTPException(403)
    name = payload.username.strip()
    if not name: raise HTTPException(422, 'ユーザー名を入力してください')
    if db.scalar(select(User).where(User.username == name)): raise HTTPException(409, 'ユーザー名が重複しています')
    u = User(username=name, password_hash=hash_password(payload.password), role=payload.role)
    db.add(u); db.commit(); db.refresh(u)
    return {'id': u.id, 'username': u.username}
