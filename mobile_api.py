

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
        raise HTTPException(status_code=401, detail="ログイン期限が切れました")
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
    return [{"id": p.id, "name": p.name, "grade": current_grade(p),
             "enrollment_year": p.enrollment_year, "note": p.note}
            for p in db.scalars(select(Player).order_by(Player.name)).all()]


@router.get("/api/public/tournaments")
def public_tournaments(db: Session = Depends(get_db)):
    tournaments = db.scalars(select(Tournament).order_by(
        Tournament.event_date.desc(), Tournament.id.desc()
    )).all()
    return [{"id": t.id, "name": t.name,
             "event_date": t.event_date.isoformat(), "venue": t.venue}
            for t in tournaments]


@router.get("/api/public/tournaments/{tournament_id}")
def public_tournament(tournament_id: int, db: Session = Depends(get_db)):
    t = db.get(Tournament, tournament_id)
