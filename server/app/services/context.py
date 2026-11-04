"""Everything a service needs, built once per process (or per test)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

from app.clients.blobs import BlobStore
from app.clients.detector import Detector, FakeDetector
from app.clients.identity import IdentityProvider
from app.clients.mailer import Mailer
from app.clients.second_opinion import SecondOpinion
from app.clients.witness_client import HttpWitnessClient, WitnessClient
from app.clock import Clock
from app.config import Config
from app.services.imagehash import JudgeCache
from app.store.codes_store import CodesStore
from app.store.devices_store import DevicesStore
from app.store.events_store import EventsStore
from app.store.ledger_store import LedgerStore
from app.store.people_store import PeopleStore
from app.store.settings_store import SettingsStore


@dataclass
class Context:
    config: Config
    conn: sqlite3.Connection
    clock: Clock
    identity: IdentityProvider
    mailer: Mailer
    second_opinion: SecondOpinion
    blobs: BlobStore
    settings: SettingsStore
    people: PeopleStore
    devices: DevicesStore
    events: EventsStore
    codes: CodesStore
    ledger: LedgerStore = None  # type: ignore[assignment]   set by build
    judge_cache: JudgeCache = field(default_factory=JudgeCache)
    detector: Detector = field(default_factory=FakeDetector)
    witness: WitnessClient = field(default_factory=HttpWitnessClient)
    models_verified: int = 0        # manifest files present with the right sha256, counted at startup

    @classmethod
    def build(cls, config: Config, conn: sqlite3.Connection, clock: Clock, identity: IdentityProvider,
              mailer: Mailer, second_opinion: SecondOpinion, blobs: BlobStore) -> Context:
        ctx = cls(
            config=config, conn=conn, clock=clock, identity=identity, mailer=mailer,
            second_opinion=second_opinion, blobs=blobs,
            settings=SettingsStore(conn, clock), people=PeopleStore(conn, clock),
            devices=DevicesStore(conn, clock), events=EventsStore(conn, clock),
            codes=CodesStore(conn, clock), ledger=LedgerStore(conn, clock),
        )
        from app.services import ledger
        ledger.start(ctx)
        return ctx
