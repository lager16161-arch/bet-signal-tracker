"""SQLite persistence, duplicate review, and reports for offline signals."""

import re
import sqlite3
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from channel_mapping import classify_channel
from parser import parse_signal


DEFAULT_DATABASE_PATH = Path(__file__).resolve().with_name("tracker.db")
_VALID_RESULTS = {"win", "loss", "void", "unknown"}
_VALID_BET_TYPES = {"single", "parlay", "unknown"}
_VALID_SIGNAL_SIZE_TYPES = {"large", "regular", "unknown"}
_FIXED_STAKE = Decimal("100")

SIGNAL_FIELDS = (
    "channel_name",
    "source_channel_id",
    "message_id",
    "sport",
    "channel_type",
    "bet_type",
    "signal_size_type",
    "express_leg_count",
    "stake_amount",
    "currency",
    "odds",
    "payout",
    "payout_currency",
    "match_event",
    "competition",
    "start_time_text",
    "market",
    "selection",
    "prematch_live",
    "coefficient_color",
    "result",
    "potential_duplicate",
    "potential_conflict",
    "original_message",
    "created_at",
    "updated_at",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel_name TEXT NOT NULL,
    message_id TEXT,
    sport TEXT,
    prematch_live TEXT,
    stake_amount TEXT,
    currency TEXT,
    odds TEXT,
    payout TEXT,
    payout_currency TEXT,
    match_event TEXT,
    competition TEXT,
    start_time_text TEXT,
    market TEXT,
    selection TEXT,
    coefficient_color TEXT,
    result TEXT NOT NULL
        CHECK (result IN ('win', 'loss', 'void', 'unknown')),
    original_message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (channel_name, message_id)
)
"""

_MIGRATIONS = {
    "source_channel_id": "TEXT",
    "channel_type": "TEXT NOT NULL DEFAULT 'unknown'",
    "bet_type": "TEXT NOT NULL DEFAULT 'unknown'",
    "potential_duplicate": "INTEGER NOT NULL DEFAULT 0",
    "potential_conflict": "INTEGER NOT NULL DEFAULT 0",
    "signal_size_type": "TEXT NOT NULL DEFAULT 'unknown'",
    "express_leg_count": "INTEGER",
}


def _path(db_path: Optional[Union[str, Path]]) -> Path:
    return Path(db_path) if db_path is not None else DEFAULT_DATABASE_PATH


def _connect(db_path: Optional[Union[str, Path]] = None) -> sqlite3.Connection:
    connection = sqlite3.connect(str(_path(db_path)))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _as_text(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def initialize_database(
    db_path: Optional[Union[str, Path]] = None,
) -> Path:
    """Create a database or add new columns without replacing existing records."""
    database_path = _path(db_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(_connect(database_path)) as connection:
        with connection:
            connection.execute(_SCHEMA)
            existing_columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(signals)")
            }
            existing_tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            backfill_signal_size_type = "signal_size_type" not in existing_columns
            backfill_express_data = (
                "express_leg_count" not in existing_columns
                or "express_legs" not in existing_tables
            )
            for column, definition in _MIGRATIONS.items():
                if column not in existing_columns:
                    connection.execute(
                        "ALTER TABLE signals ADD COLUMN {} {}".format(
                            column, definition
                        )
                    )
            if backfill_signal_size_type:
                old_records = connection.execute(
                    "SELECT id, original_message FROM signals"
                ).fetchall()
                for record in old_records:
                    signal_size_type = parse_signal(
                        record["original_message"]
                    ).get("signal_size_type", "unknown")
                    connection.execute(
                        "UPDATE signals SET signal_size_type = ? WHERE id = ?",
                        (signal_size_type, record["id"]),
                    )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS express_legs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL,
                    leg_number INTEGER NOT NULL,
                    sport TEXT,
                    match_event TEXT,
                    competition TEXT,
                    start_time_text TEXT,
                    market TEXT,
                    selection TEXT,
                    odds TEXT,
                    raw_text TEXT NOT NULL,
                    UNIQUE (signal_id, leg_number),
                    FOREIGN KEY (signal_id) REFERENCES signals(id)
                        ON DELETE CASCADE
                )
                """
            )
            if backfill_express_data:
                old_records = connection.execute(
                    """
                    SELECT id, original_message, bet_type, signal_size_type,
                           express_leg_count, odds
                    FROM signals
                    """
                ).fetchall()
                for record in old_records:
                    parsed = parse_signal(record["original_message"])
                    updates = {}
                    parsed_bet_type = parsed.get("bet_type", "unknown")
                    if (
                        record["bet_type"] == "unknown"
                        and parsed_bet_type in {"single", "parlay"}
                    ):
                        updates["bet_type"] = parsed_bet_type

                    if (
                        record["signal_size_type"] == "unknown"
                        and parsed.get("signal_size_type")
                        in {"large", "regular"}
                    ):
                        updates["signal_size_type"] = parsed[
                            "signal_size_type"
                        ]

                    is_express = (
                        updates.get("bet_type", record["bet_type"]) == "parlay"
                        and parsed_bet_type == "parlay"
                    )
                    if is_express:
                        if (
                            record["express_leg_count"] is None
                            and parsed.get("express_leg_count") is not None
                        ):
                            updates["express_leg_count"] = parsed[
                                "express_leg_count"
                            ]
                        if not record["odds"] and parsed.get("odds") is not None:
                            updates["odds"] = _as_text(parsed["odds"])

                        existing_leg = connection.execute(
                            """
                            SELECT 1 FROM express_legs
                            WHERE signal_id = ? LIMIT 1
                            """,
                            (record["id"],),
                        ).fetchone()
                        if not existing_leg:
                            for leg in parsed.get("express_legs", []):
                                connection.execute(
                                    """
                                    INSERT OR IGNORE INTO express_legs (
                                        signal_id, leg_number, sport,
                                        match_event, competition,
                                        start_time_text, market, selection,
                                        odds, raw_text
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        record["id"],
                                        leg["leg_number"],
                                        _as_text(leg.get("sport")),
                                        _as_text(leg.get("match_event")),
                                        _as_text(leg.get("competition")),
                                        _as_text(leg.get("start_time_text")),
                                        _as_text(leg.get("market")),
                                        _as_text(leg.get("selection")),
                                        _as_text(leg.get("odds")),
                                        leg.get("raw_text") or "",
                                    ),
                                )

                    if updates:
                        connection.execute(
                            "UPDATE signals SET {} WHERE id = ?".format(
                                ", ".join(
                                    "{} = ?".format(column) for column in updates
                                )
                            ),
                            list(updates.values()) + [record["id"]],
                        )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS ix_express_legs_signal_id
                ON express_legs (signal_id)
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    uq_signals_source_channel_message
                ON signals (source_channel_id, message_id)
                WHERE source_channel_id IS NOT NULL
                  AND source_channel_id <> ''
                  AND message_id IS NOT NULL
                  AND message_id <> ''
                """
            )
    return database_path


def reclassify_signals(
    mapping: Dict[str, Dict[str, str]],
    db_path: Optional[Union[str, Path]] = None,
) -> None:
    """Refresh derived sport/type values using only the configured source map."""
    initialize_database(db_path)
    with closing(_connect(db_path)) as connection:
        with connection:
            records = connection.execute(
                "SELECT id, source_channel_id, channel_name FROM signals"
            ).fetchall()
            for record in records:
                classification = classify_channel(
                    record["source_channel_id"], record["channel_name"], mapping
                )
                channel_type = classification["channel_type"]
                connection.execute(
                    """
                    UPDATE signals
                    SET sport = ?, channel_type = ?, prematch_live = ?
                    WHERE id = ?
                    """,
                    (
                        classification["sport"],
                        channel_type,
                        channel_type,
                        record["id"],
                    ),
                )
    recompute_review_flags(db_path)


def insert_signal(
    channel_name: str,
    message_id: Optional[Union[str, int]],
    parsed_signal: Dict[str, Any],
    original_message: str,
    db_path: Optional[Union[str, Path]] = None,
    source_channel_id: Optional[Union[str, int]] = None,
    classification: Optional[Dict[str, str]] = None,
    bet_type: Optional[str] = None,
) -> int:
    """Save one signal, upserting repeated source-channel/message pairs."""
    channel_name = channel_name.strip()
    if not channel_name:
        raise ValueError("channel_name must not be empty")
    if original_message is None:
        raise ValueError("original_message must not be None")

    normalized_message_id = (
        str(message_id).strip() if message_id is not None else ""
    ) or None
    normalized_channel_id = (
        str(source_channel_id).strip() if source_channel_id is not None else ""
    ) or None
    result = parsed_signal.get("result") or "unknown"
    if result not in _VALID_RESULTS:
        raise ValueError("result must be win, loss, void, or unknown")
    signal_size_type = parsed_signal.get("signal_size_type", "unknown")
    if signal_size_type not in _VALID_SIGNAL_SIZE_TYPES:
        raise ValueError(
            "signal_size_type must be large, regular, or unknown"
        )
    bet_type = bet_type or parsed_signal.get("bet_type", "unknown")
    if bet_type not in _VALID_BET_TYPES:
        raise ValueError("bet_type must be single, parlay, or unknown")
    express_legs = parsed_signal.get("express_legs") or []

    classification = classification or {
        "sport": "unknown",
        "channel_type": "unknown",
    }
    sport = classification.get("sport", "unknown")
    channel_type = classification.get("channel_type", "unknown")
    if not sport or not channel_type:
        sport, channel_type = "unknown", "unknown"

    now = _timestamp()
    values = {
        "channel_name": channel_name,
        "source_channel_id": normalized_channel_id,
        "message_id": normalized_message_id,
        "sport": sport,
        "channel_type": channel_type,
        "bet_type": bet_type,
        "signal_size_type": signal_size_type,
        "express_leg_count": parsed_signal.get("express_leg_count"),
        "stake_amount": _as_text(parsed_signal.get("stake")),
        "currency": _as_text(parsed_signal.get("currency")),
        "odds": _as_text(parsed_signal.get("odds")),
        "payout": _as_text(parsed_signal.get("possible_payout")),
        "payout_currency": _as_text(parsed_signal.get("payout_currency")),
        "match_event": _as_text(parsed_signal.get("match_event")),
        "competition": _as_text(parsed_signal.get("competition")),
        "start_time_text": _as_text(parsed_signal.get("start_time")),
        "market": _as_text(parsed_signal.get("market")),
        "selection": _as_text(parsed_signal.get("selection")),
        "prematch_live": channel_type,
        "coefficient_color": _as_text(parsed_signal.get("coefficient_color")),
        "result": result,
        "original_message": original_message,
        "updated_at": now,
    }

    initialize_database(db_path)
    with closing(_connect(db_path)) as connection:
        with connection:
            existing_id = None
            if normalized_message_id:
                if normalized_channel_id:
                    rows = connection.execute(
                        """
                        SELECT DISTINCT id
                        FROM signals
                        WHERE message_id = ?
                          AND (source_channel_id = ? OR channel_name = ?)
                        """,
                        (
                            normalized_message_id,
                            normalized_channel_id,
                            channel_name,
                        ),
                    ).fetchall()
                else:
                    rows = connection.execute(
                        """
                        SELECT id FROM signals
                        WHERE channel_name = ? AND message_id = ?
                        """,
                        (channel_name, normalized_message_id),
                    ).fetchall()
                if len(rows) > 1:
                    raise ValueError(
                        "channel/message matches multiple existing records; "
                        "review them before importing"
                    )
                if rows:
                    existing_id = rows[0]["id"]

            if existing_id is None:
                insert_values = dict(values)
                insert_values["created_at"] = now
                columns = tuple(insert_values)
                cursor = connection.execute(
                    "INSERT INTO signals ({}) VALUES ({})".format(
                        ", ".join(columns),
                        ", ".join("?" for _ in columns),
                    ),
                    [insert_values[column] for column in columns],
                )
                signal_id = int(cursor.lastrowid)
            else:
                columns = tuple(values)
                connection.execute(
                    "UPDATE signals SET {} WHERE id = ?".format(
                        ", ".join("{} = ?".format(column) for column in columns)
                    ),
                    [values[column] for column in columns] + [existing_id],
                )
                signal_id = int(existing_id)

            connection.execute(
                "DELETE FROM express_legs WHERE signal_id = ?", (signal_id,)
            )
            for leg in express_legs:
                connection.execute(
                    """
                    INSERT INTO express_legs (
                        signal_id, leg_number, sport, match_event, competition,
                        start_time_text, market, selection, odds, raw_text
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal_id,
                        leg["leg_number"],
                        _as_text(leg.get("sport")),
                        _as_text(leg.get("match_event")),
                        _as_text(leg.get("competition")),
                        _as_text(leg.get("start_time_text")),
                        _as_text(leg.get("market")),
                        _as_text(leg.get("selection")),
                        _as_text(leg.get("odds")),
                        leg.get("raw_text") or "",
                    ),
                )

    recompute_review_flags(db_path)
    return signal_id


def _normalize(value: Optional[str]) -> str:
    return " ".join((value or "").split()).casefold()


def _are_opposite_selections(
    first: str, second: str, event: str, market: str
) -> bool:
    left, right = _normalize(first), _normalize(second)
    if not left or not right or left == right:
        return False

    opposite_pairs = (
        {"over", "under"},
        {"больше", "меньше"},
        {"yes", "no"},
        {"да", "нет"},
        {"true", "false"},
        {"п1", "п2"},
        {"1", "2"},
        {"home", "away"},
        {"хозяева", "гости"},
        {"home win", "away win"},
        {"team 1", "team 2"},
        {"player 1", "player 2"},
    )
    if {left, right} in opposite_pairs:
        return True

    over_under_pattern = re.compile(r"^(over|under|больше|меньше)\s+(.+)$")
    left_match = over_under_pattern.match(left)
    right_match = over_under_pattern.match(right)
    if left_match and right_match:
        left_term, left_line = left_match.groups()
        right_term, right_line = right_match.groups()
        opposing_terms = {
            ("over", "under"),
            ("больше", "меньше"),
        }
        if (
            (left_term, right_term) in opposing_terms
            and left_line == right_line
        ) or (
            (right_term, left_term) in opposing_terms
            and left_line == right_line
        ):
            return True

    market_text = _normalize(market)
    is_winner_market = any(
        term in market_text
        for term in ("winner", "победитель", "moneyline", "исход матча")
    )
    if is_winner_market:
        participants = {
            _normalize(participant)
            for participant in re.split(
                r"\s+(?:-|–|—|vs\.?|versus|против)\s+",
                event,
                flags=re.IGNORECASE,
            )
            if _normalize(participant)
        }
        if left in participants and right in participants:
            return True
    return False


def recompute_review_flags(
    db_path: Optional[Union[str, Path]] = None,
) -> None:
    """Flag exact repeated picks and recognized opposite selections for review."""
    initialize_database(db_path)
    with closing(_connect(db_path)) as connection:
        records = connection.execute(
            "SELECT id, bet_type, match_event, market, selection FROM signals"
        ).fetchall()

    flags = {record["id"]: [0, 0] for record in records}
    by_pick = defaultdict(list)
    by_event_market = defaultdict(list)
    for record in records:
        if record["bet_type"] != "single":
            continue
        event = _normalize(record["match_event"])
        market = _normalize(record["market"])
        selection = _normalize(record["selection"])
        if event and market and selection:
            by_pick[(event, market, selection)].append(record)
            by_event_market[(event, market)].append(record)

    for group in by_pick.values():
        if len(group) > 1:
            for record in group:
                flags[record["id"]][0] = 1

    for (event, market), group in by_event_market.items():
        for index, first in enumerate(group):
            for second in group[index + 1 :]:
                if _are_opposite_selections(
                    first["selection"],
                    second["selection"],
                    first["match_event"],
                    first["market"],
                ):
                    flags[first["id"]][1] = 1
                    flags[second["id"]][1] = 1

    with closing(_connect(db_path)) as connection:
        with connection:
            for signal_id, (duplicate, conflict) in flags.items():
                connection.execute(
                    """
                    UPDATE signals
                    SET potential_duplicate = ?, potential_conflict = ?
                    WHERE id = ?
                    """,
                    (duplicate, conflict, signal_id),
                )


def list_signals(
    db_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Return all saved records in insertion order."""
    initialize_database(db_path)
    with closing(_connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT id, {} FROM signals ORDER BY id".format(
                ", ".join(SIGNAL_FIELDS)
            )
        ).fetchall()
    return [dict(row) for row in rows]


def list_express_legs(
    signal_id: Optional[int] = None,
    db_path: Optional[Union[str, Path]] = None,
) -> List[Dict[str, Any]]:
    """Return stored legs, optionally restricted to one main signal."""
    initialize_database(db_path)
    with closing(_connect(db_path)) as connection:
        if signal_id is None:
            rows = connection.execute(
                "SELECT * FROM express_legs ORDER BY signal_id, leg_number"
            ).fetchall()
        else:
            rows = connection.execute(
                """
                SELECT * FROM express_legs
                WHERE signal_id = ?
                ORDER BY leg_number
                """,
                (signal_id,),
            ).fetchall()
    return [dict(row) for row in rows]


def _bet_category(record: Dict[str, Any]) -> str:
    categories = {
        ("single", "regular"): "regular_single",
        ("single", "large"): "large_single",
        ("parlay", "regular"): "regular_express",
        ("parlay", "large"): "large_express",
    }
    return categories.get(
        (record.get("bet_type"), record.get("signal_size_type")), "unknown"
    )


def _express_leg_analytics(
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    with closing(_connect(db_path)) as connection:
        express_rows = connection.execute(
            """
            SELECT id, express_leg_count FROM signals
            WHERE bet_type = 'parlay'
            ORDER BY id
            """
        ).fetchall()
        legs = connection.execute(
            """
            SELECT legs.sport, legs.market, legs.signal_id
            FROM express_legs AS legs
            JOIN signals ON signals.id = legs.signal_id
            WHERE signals.bet_type = 'parlay'
            ORDER BY legs.signal_id, legs.leg_number
            """
        ).fetchall()

    parsed_counts = defaultdict(int)
    by_sport = defaultdict(int)
    by_market = defaultdict(int)
    for leg in legs:
        parsed_counts[leg["signal_id"]] += 1
        by_sport[leg["sport"] or "unknown"] += 1
        by_market[leg["market"] or "unknown"] += 1

    declared_legs = 0
    count_mismatches = 0
    for express in express_rows:
        declared = express["express_leg_count"]
        if declared is not None:
            declared_legs += int(declared)
            if int(declared) != parsed_counts[express["id"]]:
                count_mismatches += 1

    return {
        "express_bets": len(express_rows),
        "declared_legs": declared_legs,
        "parsed_legs": len(legs),
        "bets_with_leg_count_mismatch": count_mismatches,
        "by_sport": dict(sorted(by_sport.items())),
        "by_market": dict(sorted(by_market.items())),
    }


def _empty_metrics() -> Dict[str, Any]:
    return {
        "total": 0,
        "settled": 0,
        "wins": 0,
        "losses": 0,
        "voids": 0,
        "unknown": 0,
        "profit_rub": Decimal("0"),
        "settled_stake_rub": Decimal("0"),
        "yield_percent": Decimal("0"),
        "settled_profit_rub": Decimal("0"),
        "roi_percent": Decimal("0"),
        "wins_without_odds": 0,
    }


def _add_record_to_metrics(metrics: Dict[str, Any], record: Dict[str, Any]) -> None:
    metrics["total"] += 1
    result = record["result"]
    if result == "win":
        metrics["wins"] += 1
    elif result == "loss":
        metrics["losses"] += 1
    elif result == "void":
        metrics["voids"] += 1
    else:
        metrics["unknown"] += 1
        return

    metrics["settled"] += 1
    metrics["settled_stake_rub"] += _FIXED_STAKE
    if result == "loss":
        metrics["profit_rub"] -= _FIXED_STAKE
    elif result == "win":
        try:
            odds = Decimal(record["odds"]) if record["odds"] else None
        except InvalidOperation:
            odds = None
        if odds is None:
            metrics["wins_without_odds"] += 1
        else:
            metrics["profit_rub"] += _FIXED_STAKE * (odds - Decimal("1"))


def _finish_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    metrics["yield_percent"] = (
        metrics["profit_rub"] / metrics["settled_stake_rub"] * Decimal("100")
        if metrics["settled_stake_rub"]
        else Decimal("0")
    )
    metrics["settled_profit_rub"] = metrics["profit_rub"]
    metrics["roi_percent"] = metrics["yield_percent"]
    return metrics


def build_report(
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Build overall metrics and breakdowns from stored, classified fields."""
    records = list_signals(db_path)
    dimensions = (
        "sport",
        "channel_type",
        "coefficient_color",
        "bet_type",
        "signal_size_type",
        "bet_category",
        "result",
    )
    overall = _empty_metrics()
    breakdowns = {dimension: defaultdict(_empty_metrics) for dimension in dimensions}
    for category in (
        "regular_single",
        "large_single",
        "regular_express",
        "large_express",
    ):
        breakdowns["bet_category"][category]
    for size_type in ("large", "regular", "unknown"):
        breakdowns["signal_size_type"][size_type]

    for record in records:
        record["bet_category"] = _bet_category(record)
        _add_record_to_metrics(overall, record)
        for dimension in dimensions:
            key = record.get(dimension) or "unknown"
            _add_record_to_metrics(breakdowns[dimension][key], record)

    finalized_breakdowns = {
        dimension: {
            key: _finish_metrics(metrics)
            for key, metrics in sorted(groups.items())
        }
        for dimension, groups in breakdowns.items()
    }
    with closing(_connect(db_path)) as connection:
        flag_counts = connection.execute(
            """
            SELECT
                COALESCE(SUM(potential_duplicate), 0) AS duplicates,
                COALESCE(SUM(potential_conflict), 0) AS conflicts
            FROM signals
            """
        ).fetchone()

    return {
        "overall": _finish_metrics(overall),
        "breakdowns": finalized_breakdowns,
        "express_leg_analytics": _express_leg_analytics(db_path),
        "potential_duplicate_records": int(flag_counts["duplicates"]),
        "potential_conflict_records": int(flag_counts["conflicts"]),
    }


def get_summary(
    db_path: Optional[Union[str, Path]] = None,
) -> Dict[str, Any]:
    """Backward-compatible summary with expanded report data."""
    report = build_report(db_path)
    overall = report["overall"]
    return {
        "total": overall["total"],
        "settled": overall["settled"],
        "wins": overall["wins"],
        "losses": overall["losses"],
        "voids": overall["voids"],
        "unknown": overall["unknown"],
        "profit_rub": overall["profit_rub"],
        "settled_stake_rub": overall["settled_stake_rub"],
        "yield_percent": overall["yield_percent"],
        "settled_profit_rub": overall["settled_profit_rub"],
        "roi_percent": overall["roi_percent"],
        "wins_without_odds": overall["wins_without_odds"],
        "breakdowns": report["breakdowns"],
        "potential_duplicate_records": report["potential_duplicate_records"],
        "potential_conflict_records": report["potential_conflict_records"],
    }
