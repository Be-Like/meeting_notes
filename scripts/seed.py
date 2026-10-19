"""Seed the database with users, meetings, notes, and tasks for manual testing.

    .venv/bin/python scripts/seed.py            # fills an empty database
    .venv/bin/python scripts/seed.py --reset    # drops every table first

Honours DATABASE_URL like the app does. Every seeded user has the password below; log in through
POST /auth/login to get a token, since tokens are only ever issued by login.
"""

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from auth import hash_password  # noqa: E402
from database import SessionLocal, create_tables, engine  # noqa: E402
from models import Base, Meeting, Note, Task, User  # noqa: E402

PASSWORD = "password123"

USERS = [
    ("Ada Lovelace", "ada@example.com"),
    ("Grace Hopper", "grace@example.com"),
    ("Alan Turing", "alan@example.com"),
    ("Linus Torvalds", "linus@example.com"),
]

# (title, description, days from now, creator email, attendee emails, notes, tasks)
# Attendance is deliberately uneven so the visibility rule has something to hide: Linus attends only the
# last meeting and sees nothing else, and Alan never sees the one-on-one.
MEETINGS = [
    (
        "Sprint planning",
        "Pick the stories for the next two weeks.",
        -7,
        "ada@example.com",
        ["grace@example.com", "alan@example.com"],
        ["Velocity last sprint was 34 points.", "Grace raised the flaky CI job again."],
        [
            ("Fix the flaky integration test", "It fails about one run in five.", "completed"),
            ("Write the release notes", None, "pending"),
        ],
    ),
    (
        "Ada and Grace one-on-one",
        None,
        -2,
        "ada@example.com",
        ["grace@example.com"],
        ["Grace wants to lead the transport work."],
        [("Draft the gRPC transport proposal", "One page, due next week.", "pending")],
    ),
    (
        "Architecture review",
        "Walk through the service mesh and the transport seam.",
        1,
        "grace@example.com",
        ["ada@example.com", "alan@example.com"],
        ["Agenda: cache invalidation, error mapping, per-service transports."],
        [
            ("Benchmark JsonTransport against InMemoryTransport", None, "pending"),
            ("Document the error table", "Already in the README; confirm it matches the code.", "cancelled"),
        ],
    ),
    (
        "Retrospective",
        "What went well, what did not, what we change.",
        3,
        "alan@example.com",
        ["ada@example.com", "grace@example.com"],
        [],
        [],
    ),
    (
        "Kernel sync",
        "Linus joins to discuss the upstream patch set.",
        5,
        "grace@example.com",
        ["linus@example.com"],
        ["Linus asked for the patches to be split per subsystem."],
        [("Split the patch set", "One series per subsystem.", "pending")],
    ),
]


def seed(session) -> None:
    password_hash = hash_password(PASSWORD)
    users = {email: User(name=name, email=email, password_hash=password_hash) for name, email in USERS}
    session.add_all(users.values())
    session.flush()

    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    for title, description, days, creator, attendees, notes, tasks in MEETINGS:
        meeting = Meeting(
            title=title,
            description=description,
            scheduled_time=now + timedelta(days=days),
            created_by_id=users[creator].id,
            attendees=[users[creator], *(users[email] for email in attendees)],
            notes=[Note(content=content) for content in notes],
            tasks=[Task(title=t, description=d, status=s) for t, d, s in tasks],
        )
        session.add(meeting)
    session.commit()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="drop every table before seeding")
    args = parser.parse_args()

    if args.reset:
        Base.metadata.drop_all(bind=engine)
    create_tables()

    with SessionLocal() as session:
        if session.scalars(select(User.id)).first() is not None:
            print("the database already has users; pass --reset to start over", file=sys.stderr)
            return 1
        seed(session)
        counts = {model.__tablename__: session.query(model).count() for model in (User, Meeting, Note, Task)}

    print(f"seeded {engine.url}")
    for table, count in counts.items():
        print(f"  {table}: {count}")
    print(f"\nevery user's password is {PASSWORD!r}; log in with one of:")
    for _, email in USERS:
        print(f"  {email}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
