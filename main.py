"""Offline command-line betting signal tracker and its standard-library tests."""

import argparse
import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

import database
from channel_mapping import classify_channel, load_channel_mapping
from parser import parse_signal


SAMPLE_SIGNAL = """🧾 Ставка · Теннис
💵 Сумма 51 625,00 MYR
💵 Примерно в USD 12 633,85 USD
📊 Коэффициент 1,75
💰 Возможная выплата 90 343,75 MYR
Milavsky, Daniel - Harry Wendelken
🏆 ATP Challenger Sioux Falls, USA Men Singles
📅 Начало: сегодня, 01:10 МСК
🔴 Лайв
📝 Победитель матча — Milavsky, Daniel
🟡 ✅"""

LARGE_CS2_SIGNAL = """⚡ Крупная ставка ⚡ · CS2
💵 Сумма 224 752,00 USDC
📊 Коэффициент 1,35
💰 Возможная выплата 303 415,20 USDC
Team Spirit - FURIA Esports
🏆 PGL Masters Bucharest 2026
📅 Начало: сегодня, 16:46 МСК
🔴 Лайв
📝 Победитель матча — Team Spirit
🟢✅"""

REGULAR_EXPRESS_SIGNAL = """🎯 Экспресс · 3 матча
💵 Сумма 10 000,00 USDT
📊 Общий коэффициент 2,09
💰 Возможная выплата 20 936,42 USDT
2. ⚽ Футбол · Sevilla - Osasuna
🏆 La Liga
📅 Начало: сегодня, 23:00 МСК
⏳ До начала
📝 Double Chance — Sevilla or Draw (1,22)
3. ⚽ Футбол · Galatasaray Istanbul - Fenerbahce Istanbul
🏆 Super Lig
📅 Начало: сегодня, 21:30 МСК
⏳ До начала
📝 Double Chance — Galatasaray or Draw (1,31)
4. ⚽ Футбол · Paris Saint Germain - Olympique Lyonnais
🏆 Ligue 1
📅 Начало: сегодня, 22:30 МСК
⏳ До начала
📝 1x2 — Paris Saint Germain (1,31)
🔴✅"""

LARGE_EXPRESS_SIGNAL = """🔥 Крупный экспресс 🔥 · 3 матча
💵 Сумма 50 000,00 USDC
📊 Общий коэффициент 4,42
💰 Возможная выплата 221 200,80 USDC
6. ⚽ Футбол · Santa Clara - Benfica
🏆 Primeira Liga
📅 Начало: сегодня, 21:45 МСК
⏳ До начала
📝 Both Teams to Score — Yes (1,88)
7. ⚽ Футбол · Paris Saint Germain - Olympique Lyonnais
🏆 Ligue 1
📅 Начало: сегодня, 22:30 МСК
⏳ До начала
📝 Both Teams to Score — Yes (1,59)
8. ⚽ Футбол · Galatasaray Istanbul - Fenerbahce Istanbul
🏆 Super Lig
📅 Начало: сегодня, 21:30 МСК
⏳ До начала
📝 Both Teams to Score — Yes (1,48)
🔴❌"""


class ParserTests(unittest.TestCase):
    def test_sample_fields(self):
        parsed = parse_signal(SAMPLE_SIGNAL)
        self.assertEqual(parsed["sport"], "Теннис")
        self.assertEqual(parsed["stake"], Decimal("51625.00"))
        self.assertEqual(parsed["currency"], "MYR")
        self.assertEqual(parsed["approx_usd"], Decimal("12633.85"))
        self.assertEqual(parsed["approx_usd_currency"], "USD")
        self.assertEqual(parsed["odds"], Decimal("1.75"))
        self.assertEqual(parsed["possible_payout"], Decimal("90343.75"))
        self.assertEqual(parsed["payout_currency"], "MYR")
        self.assertEqual(parsed["match_event"], "Milavsky, Daniel - Harry Wendelken")
        self.assertEqual(
            parsed["competition"], "ATP Challenger Sioux Falls, USA Men Singles"
        )
        self.assertEqual(parsed["start_time"], "сегодня, 01:10 МСК")
        self.assertEqual(parsed["market"], "Победитель матча")
        self.assertEqual(parsed["selection"], "Milavsky, Daniel")
        self.assertEqual(parsed["mode"], "live")
        self.assertEqual(parsed["coefficient_color"], "yellow")
        self.assertEqual(parsed["result"], "win")

    def test_loss_marker(self):
        signal = SAMPLE_SIGNAL.replace("🟡 ✅", "🟡 ❌")
        self.assertEqual(parse_signal(signal)["result"], "loss")

    def test_void_marker(self):
        signal = SAMPLE_SIGNAL.replace("🟡 ✅", "🟡 ➖")
        self.assertEqual(parse_signal(signal)["result"], "void")

    def test_missing_marker_is_unknown(self):
        signal = SAMPLE_SIGNAL.replace("🟡 ✅", "🟡")
        self.assertEqual(parse_signal(signal)["result"], "unknown")

    def test_regular_tennis_signal_size(self):
        parsed = parse_signal(SAMPLE_SIGNAL)
        self.assertEqual(parsed["sport"], "Теннис")
        self.assertEqual(parsed["signal_size_type"], "regular")

    def test_large_cs2_signal_size_and_header_sport(self):
        parsed = parse_signal(LARGE_CS2_SIGNAL)
        self.assertEqual(parsed["sport"], "CS2")
        self.assertEqual(parsed["signal_size_type"], "large")
        self.assertEqual(parsed["result"], "win")

    def test_signal_size_is_not_inferred_from_amount_or_odds(self):
        ambiguous = SAMPLE_SIGNAL.replace("🧾 Ставка · Теннис", "Signal update")
        parsed = parse_signal(ambiguous)
        self.assertEqual(parsed["stake"], Decimal("51625.00"))
        self.assertEqual(parsed["odds"], Decimal("1.75"))
        self.assertEqual(parsed["signal_size_type"], "unknown")

    def test_result_marker_spacing_and_color_emoji(self):
        variants = (
            ("🟢✅", "win"),
            (" 🟢✅", "win"),
            ("🟡 ❌", "loss"),
        )
        for marker, expected in variants:
            with self.subTest(marker=marker):
                signal = SAMPLE_SIGNAL.rsplit("🟡 ✅", 1)[0] + marker
                self.assertEqual(parse_signal(signal)["result"], expected)

        color_without_result = SAMPLE_SIGNAL.rsplit("🟡 ✅", 1)[0] + "🟢"
        self.assertEqual(parse_signal(color_without_result)["result"], "unknown")

    def test_regular_express_parsing(self):
        parsed = parse_signal(REGULAR_EXPRESS_SIGNAL)
        self.assertEqual(parsed["bet_type"], "parlay")
        self.assertEqual(parsed["signal_size_type"], "regular")
        self.assertEqual(parsed["express_leg_count"], 3)
        self.assertEqual(parsed["stake"], Decimal("10000.00"))
        self.assertEqual(parsed["currency"], "USDT")
        self.assertEqual(parsed["odds"], Decimal("2.09"))
        self.assertEqual(parsed["possible_payout"], Decimal("20936.42"))
        self.assertEqual(parsed["result"], "win")
        self.assertIsNone(parsed["sport"])
        self.assertIsNone(parsed["match_event"])
        self.assertEqual(len(parsed["express_legs"]), 3)

        first_leg = parsed["express_legs"][0]
        self.assertEqual(first_leg["leg_number"], 2)
        self.assertEqual(first_leg["sport"], "Футбол")
        self.assertEqual(first_leg["match_event"], "Sevilla - Osasuna")
        self.assertEqual(first_leg["competition"], "La Liga")
        self.assertEqual(first_leg["start_time_text"], "сегодня, 23:00 МСК")
        self.assertEqual(first_leg["market"], "Double Chance")
        self.assertEqual(first_leg["selection"], "Sevilla or Draw")
        self.assertEqual(first_leg["odds"], Decimal("1.22"))
        self.assertIn("Sevilla - Osasuna", first_leg["raw_text"])

    def test_large_express_parsing(self):
        parsed = parse_signal(LARGE_EXPRESS_SIGNAL)
        self.assertEqual(parsed["bet_type"], "parlay")
        self.assertEqual(parsed["signal_size_type"], "large")
        self.assertEqual(parsed["express_leg_count"], 3)
        self.assertEqual(parsed["stake"], Decimal("50000.00"))
        self.assertEqual(parsed["currency"], "USDC")
        self.assertEqual(parsed["odds"], Decimal("4.42"))
        self.assertEqual(parsed["possible_payout"], Decimal("221200.80"))
        self.assertEqual(parsed["result"], "loss")
        self.assertEqual(len(parsed["express_legs"]), 3)
        self.assertEqual(parsed["express_legs"][0]["leg_number"], 6)
        self.assertEqual(
            parsed["express_legs"][0]["selection"], "Yes"
        )
        self.assertEqual(
            parsed["express_legs"][0]["odds"], Decimal("1.88")
        )

    def test_express_header_classification_is_explicit(self):
        ambiguous = REGULAR_EXPRESS_SIGNAL.replace(
            "🎯 Экспресс · 3 матча", "Signal update"
        )
        parsed = parse_signal(ambiguous)
        self.assertEqual(parsed["bet_type"], "unknown")
        self.assertEqual(parsed["signal_size_type"], "unknown")


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.db_path = Path(self.temporary_directory.name) / "test-tracker.db"
        self.mapping = load_channel_mapping()
        database.initialize_database(self.db_path)

    def _insert(
        self,
        message_id,
        text=SAMPLE_SIGNAL,
        channel_name="Tennis live",
        channel_id="-100tennis",
        bet_type=None,
    ):
        return database.insert_signal(
            channel_name=channel_name,
            source_channel_id=channel_id,
            message_id=message_id,
            parsed_signal=parse_signal(text),
            original_message=text,
            classification=classify_channel(
                channel_id, channel_name, self.mapping
            ),
            bet_type=bet_type,
            db_path=self.db_path,
        )

    def test_channel_mapping_overrides_message_labels(self):
        parsed = parse_signal(SAMPLE_SIGNAL)
        classification = classify_channel(
            None, "FOOTBALL", self.mapping
        )
        signal_id = database.insert_signal(
            channel_name="FOOTBALL",
            source_channel_id=None,
            message_id="football-1",
            parsed_signal=parsed,
            original_message=SAMPLE_SIGNAL,
            classification=classification,
            db_path=self.db_path,
        )
        record = database.list_signals(self.db_path)[0]
        self.assertEqual(record["id"], signal_id)
        self.assertEqual(record["sport"], "Football")
        self.assertEqual(record["channel_type"], "prematch")
        self.assertEqual(record["prematch_live"], "prematch")

        unknown = classify_channel(None, "not-configured", self.mapping)
        self.assertEqual(unknown, {"sport": "unknown", "channel_type": "unknown"})
        id_mapping = {
            "-100123": {"sport": "CS2", "channel_type": "live"}
        }
        self.assertEqual(
            classify_channel("-100123", "unrelated name", id_mapping),
            {"sport": "CS2", "channel_type": "live"},
        )

    def test_all_eight_channel_map_entries(self):
        expected = {
            "⚽ Футбол — До начала": {
                "sport": "Football",
                "channel_type": "prematch",
            },
            "🔴 Футбол — Лайв": {"sport": "Football", "channel_type": "live"},
            "🎾 Теннис — До начала": {
                "sport": "Tennis",
                "channel_type": "prematch",
            },
            "🔴 Теннис — Лайв": {"sport": "Tennis", "channel_type": "live"},
            "🎮 CS2 — До начала": {"sport": "CS2", "channel_type": "prematch"},
            "🔴 CS2 — Лайв": {"sport": "CS2", "channel_type": "live"},
            "👾 Dota 2 — До начала": {
                "sport": "Dota 2",
                "channel_type": "prematch",
            },
            "🔴 Dota 2 — Лайв": {"sport": "Dota 2", "channel_type": "live"},
        }
        for channel_name, classification in expected.items():
            with self.subTest(channel_name=channel_name):
                self.assertEqual(self.mapping[channel_name], classification)
                self.assertEqual(
                    classify_channel(None, channel_name, self.mapping),
                    classification,
                )
                case_and_spacing_variant = "  {}  ".format(
                    "   ".join(channel_name.upper().split())
                )
                self.assertEqual(
                    classify_channel(
                        None, case_and_spacing_variant, self.mapping
                    ),
                    classification,
                )

        legacy_aliases = {
            "FOOTBALL": {"sport": "Football", "channel_type": "prematch"},
            "FOOTBALL LIVE": {"sport": "Football", "channel_type": "live"},
            "TENNIS": {"sport": "Tennis", "channel_type": "prematch"},
            "TENNIS LIVE": {"sport": "Tennis", "channel_type": "live"},
            "CS2": {"sport": "CS2", "channel_type": "prematch"},
            "CS2 LIVE": {"sport": "CS2", "channel_type": "live"},
            "DOTA 2": {"sport": "Dota 2", "channel_type": "prematch"},
            "DOTA 2 LIVE": {"sport": "Dota 2", "channel_type": "live"},
        }
        for channel_name, classification in legacy_aliases.items():
            with self.subTest(legacy_channel_name=channel_name):
                self.assertEqual(
                    classify_channel(None, channel_name, self.mapping),
                    classification,
                )

    def test_profit_rules_and_report_breakdowns(self):
        self._insert("win", bet_type="single")
        self._insert(
            "loss",
            text=SAMPLE_SIGNAL.replace("🟡 ✅", "🟡 ❌"),
            channel_name="Football prematch",
            channel_id="-100football",
            bet_type="parlay",
        )
        self._insert(
            "void",
            text=SAMPLE_SIGNAL.replace("🟡 ✅", "🟡 ➖"),
            channel_name="CS2 live",
            channel_id="-100cs2",
            bet_type="unknown",
        )
        self._insert(
            "unknown",
            text=SAMPLE_SIGNAL.replace("🟡 ✅", "🟡"),
            channel_name="Dota 2 prematch",
            channel_id="-100dota",
            bet_type="single",
        )

        report = database.build_report(self.db_path)
        overall = report["overall"]
        self.assertEqual(overall["total"], 4)
        self.assertEqual(overall["settled"], 3)
        self.assertEqual(overall["wins"], 1)
        self.assertEqual(overall["losses"], 1)
        self.assertEqual(overall["voids"], 1)
        self.assertEqual(overall["unknown"], 1)
        self.assertEqual(overall["profit_rub"], Decimal("-25.00"))
        self.assertEqual(overall["settled_stake_rub"], Decimal("300"))

        breakdowns = report["breakdowns"]
        self.assertIn("Tennis", breakdowns["sport"])
        self.assertIn("live", breakdowns["channel_type"])
        self.assertIn("yellow", breakdowns["coefficient_color"])
        self.assertIn("single", breakdowns["bet_type"])
        self.assertIn("unknown", breakdowns["result"])

    def test_signal_size_storage_and_report(self):
        self._insert("regular-size", text=SAMPLE_SIGNAL)
        self._insert(
            "regular-void",
            text=SAMPLE_SIGNAL.replace("🟡 ✅", "🟡 ➖"),
        )
        self._insert(
            "large-size",
            text=LARGE_CS2_SIGNAL,
            channel_name="CS2 live",
            channel_id="-100cs2-size",
        )
        self._insert(
            "large-loss",
            text=LARGE_CS2_SIGNAL.replace("🟢✅", "🟢❌"),
            channel_name="CS2 live",
            channel_id="-100cs2-size",
        )
        ambiguous = SAMPLE_SIGNAL.replace(
            "🧾 Ставка · Теннис", "Signal update"
        ).replace("🟡 ✅", "🟡")
        self._insert(
            "unknown-size",
            text=ambiguous,
            channel_name="Tennis live",
            channel_id="-100tennis-unknown-size",
        )

        records = {
            record["message_id"]: record
            for record in database.list_signals(self.db_path)
        }
        self.assertEqual(records["regular-size"]["signal_size_type"], "regular")
        self.assertEqual(records["large-size"]["signal_size_type"], "large")
        self.assertEqual(records["unknown-size"]["signal_size_type"], "unknown")

        size_breakdown = database.build_report(self.db_path)["breakdowns"][
            "signal_size_type"
        ]
        self.assertEqual(set(size_breakdown), {"large", "regular", "unknown"})
        self.assertEqual(size_breakdown["large"]["total"], 2)
        self.assertEqual(size_breakdown["large"]["wins"], 1)
        self.assertEqual(size_breakdown["large"]["losses"], 1)
        self.assertEqual(
            size_breakdown["large"]["settled_profit_rub"], Decimal("-65.00")
        )
        self.assertEqual(size_breakdown["large"]["roi_percent"], Decimal("-32.5"))
        self.assertEqual(size_breakdown["regular"]["total"], 2)
        self.assertEqual(size_breakdown["regular"]["wins"], 1)
        self.assertEqual(size_breakdown["regular"]["voids"], 1)
        self.assertEqual(
            size_breakdown["regular"]["settled_profit_rub"], Decimal("75.00")
        )
        self.assertEqual(size_breakdown["regular"]["roi_percent"], Decimal("37.5"))
        self.assertEqual(size_breakdown["unknown"]["total"], 1)
        self.assertEqual(size_breakdown["unknown"]["unknown"], 1)
        self.assertEqual(size_breakdown["unknown"]["settled"], 0)
        self.assertEqual(size_breakdown["unknown"]["roi_percent"], Decimal("0"))

    def test_express_storage_reports_and_leg_duplicate_isolation(self):
        regular_id = self._insert(
            "regular-express-1",
            text=REGULAR_EXPRESS_SIGNAL,
            channel_name="FOOTBALL",
            channel_id="-100football-express",
        )
        large_id = self._insert(
            "large-express-1",
            text=LARGE_EXPRESS_SIGNAL,
            channel_name="FOOTBALL LIVE",
            channel_id="-100football-express-live",
        )
        duplicate_overlap_id = self._insert(
            "regular-express-2",
            text=REGULAR_EXPRESS_SIGNAL,
            channel_name="FOOTBALL",
            channel_id="-100football-express",
        )

        records = {
            record["id"]: record for record in database.list_signals(self.db_path)
        }
        self.assertEqual(records[regular_id]["bet_type"], "parlay")
        self.assertEqual(records[regular_id]["signal_size_type"], "regular")
        self.assertEqual(records[regular_id]["express_leg_count"], 3)
        self.assertEqual(records[regular_id]["sport"], "Football")
        self.assertEqual(records[regular_id]["channel_type"], "prematch")
        self.assertEqual(records[regular_id]["odds"], "2.09")
        self.assertEqual(records[large_id]["bet_type"], "parlay")
        self.assertEqual(records[large_id]["signal_size_type"], "large")
        self.assertEqual(records[large_id]["result"], "loss")

        legs = database.list_express_legs(regular_id, self.db_path)
        self.assertEqual(len(legs), 3)
        self.assertEqual(legs[0]["sport"], "Футбол")
        self.assertEqual(legs[0]["odds"], "1.22")
        self.assertIn("Sevilla - Osasuna", legs[0]["raw_text"])
        for signal_id in (regular_id, large_id, duplicate_overlap_id):
            self.assertEqual(records[signal_id]["potential_duplicate"], 0)
            self.assertEqual(records[signal_id]["potential_conflict"], 0)

        with sqlite3.connect(str(self.db_path)) as connection:
            connection.execute(
                """
                UPDATE signals
                SET match_event = ?, market = ?, selection = ?
                WHERE bet_type = 'parlay'
                """,
                ("Sevilla - Osasuna", "Double Chance", "Sevilla or Draw"),
            )
        database.recompute_review_flags(self.db_path)
        records = {
            record["id"]: record for record in database.list_signals(self.db_path)
        }
        for signal_id in (regular_id, large_id, duplicate_overlap_id):
            self.assertEqual(records[signal_id]["potential_duplicate"], 0)
            self.assertEqual(records[signal_id]["potential_conflict"], 0)

        report = database.build_report(self.db_path)
        self.assertEqual(report["overall"]["total"], 3)
        self.assertEqual(report["overall"]["wins"], 2)
        self.assertEqual(report["overall"]["losses"], 1)
        self.assertEqual(report["overall"]["profit_rub"], Decimal("118.00"))
        categories = report["breakdowns"]["bet_category"]
        self.assertEqual(
            set(categories),
            {
                "regular_single",
                "large_single",
                "regular_express",
                "large_express",
            },
        )
        self.assertEqual(categories["regular_express"]["total"], 2)
        self.assertEqual(
            categories["regular_express"]["settled_profit_rub"],
            Decimal("218.00"),
        )
        self.assertEqual(
            categories["large_express"]["settled_profit_rub"],
            Decimal("-100.00"),
        )
        leg_analytics = report["express_leg_analytics"]
        self.assertEqual(leg_analytics["express_bets"], 3)
        self.assertEqual(leg_analytics["declared_legs"], 9)
        self.assertEqual(leg_analytics["parsed_legs"], 9)
        self.assertEqual(leg_analytics["bets_with_leg_count_mismatch"], 0)
        self.assertEqual(leg_analytics["by_sport"], {"Футбол": 9})

    def test_express_example_profit_uses_overall_coefficient_once_per_bet(self):
        regular_id = self._insert(
            "profit-regular-express",
            text=REGULAR_EXPRESS_SIGNAL,
            channel_name="Football prematch",
            channel_id="-100football-profit",
        )
        large_id = self._insert(
            "profit-large-express",
            text=LARGE_EXPRESS_SIGNAL,
            channel_name="Football live",
            channel_id="-100football-profit-live",
        )
        report = database.build_report(self.db_path)
        self.assertEqual(report["overall"]["total"], 2)
        self.assertEqual(report["overall"]["wins"], 1)
        self.assertEqual(report["overall"]["losses"], 1)
        self.assertEqual(report["overall"]["profit_rub"], Decimal("9.00"))
        categories = report["breakdowns"]["bet_category"]
        self.assertEqual(
            categories["regular_express"]["settled_profit_rub"],
            Decimal("109.00"),
        )
        self.assertEqual(
            categories["large_express"]["settled_profit_rub"],
            Decimal("-100.00"),
        )
        self.assertEqual(
            len(database.list_signals(self.db_path)), 2
        )
        self.assertEqual(
            len(database.list_express_legs(regular_id, self.db_path)), 3
        )
        self.assertEqual(
            len(database.list_express_legs(large_id, self.db_path)), 3
        )
        self.assertEqual(report["express_leg_analytics"]["parsed_legs"], 6)

    def test_express_message_edit_replaces_legs_without_adding_a_bet(self):
        signal_id = self._insert(
            "edited-express",
            text=REGULAR_EXPRESS_SIGNAL,
            channel_name="Football prematch",
            channel_id="-100football-edits",
        )
        original_created_at = database.list_signals(self.db_path)[0]["created_at"]
        edited_text = REGULAR_EXPRESS_SIGNAL.replace(
            "Sevilla or Draw", "Sevilla"
        ).replace("(1,22)", "(1,40)").replace("🔴✅", "🔴❌")
        repeated_id = self._insert(
            "edited-express",
            text=edited_text,
            channel_name="Football prematch",
            channel_id="-100football-edits",
        )

        records = database.list_signals(self.db_path)
        legs = database.list_express_legs(signal_id, self.db_path)
        self.assertEqual(signal_id, repeated_id)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["created_at"], original_created_at)
        self.assertEqual(records[0]["result"], "loss")
        self.assertEqual(records[0]["original_message"], edited_text)
        self.assertEqual(len(legs), 3)
        self.assertEqual(legs[0]["selection"], "Sevilla")
        self.assertEqual(legs[0]["odds"], "1.40")

    def test_duplicate_import_and_review_flags(self):
        first_id = self._insert("message-1")
        repeated_id = self._insert("message-1")
        self.assertEqual(first_id, repeated_id)
        self.assertEqual(len(database.list_signals(self.db_path)), 1)

        duplicate_id = self._insert("message-2")
        opposite_text = SAMPLE_SIGNAL.replace(
            "📝 Победитель матча — Milavsky, Daniel",
            "📝 Победитель матча — Harry Wendelken",
        )
        conflict_id = self._insert("message-3", text=opposite_text)
        records = {row["id"]: row for row in database.list_signals(self.db_path)}
        self.assertTrue(records[first_id]["potential_duplicate"])
        self.assertTrue(records[duplicate_id]["potential_duplicate"])
        self.assertTrue(records[first_id]["potential_conflict"])
        self.assertTrue(records[duplicate_id]["potential_conflict"])
        self.assertTrue(records[conflict_id]["potential_conflict"])
        self.assertEqual(len(records), 3)

    def test_legacy_migration_preserves_existing_record(self):
        legacy_path = Path(self.temporary_directory.name) / "legacy.db"
        connection = sqlite3.connect(str(legacy_path))
        try:
            connection.executescript(
                """
                CREATE TABLE signals (
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
                    result TEXT NOT NULL,
                    original_message TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE (channel_name, message_id)
                );
                INSERT INTO signals (
                    id, channel_name, sport, prematch_live, odds, result,
                    original_message, created_at, updated_at
                ) VALUES (
                    9, 'offline-sample', 'Tennis', 'live', '1.75', 'win',
                    'original text', 'created', 'updated'
                );
                INSERT INTO signals (
                    id, channel_name, sport, prematch_live, odds, result,
                    original_message, created_at, updated_at
                ) VALUES (
                    10, 'offline-sample-2', 'Tennis', 'live', '1.75', 'win',
                    '""" + SAMPLE_SIGNAL.replace("'", "''") + """',
                    'created-2', 'updated-2'
                );
                INSERT INTO signals (
                    id, channel_name, result, original_message, created_at,
                    updated_at
                ) VALUES (
                    11, 'offline-express', 'win',
                    '""" + REGULAR_EXPRESS_SIGNAL.replace("'", "''") + """',
                    'created-3', 'updated-3'
                );
                """
            )
            connection.commit()
        finally:
            connection.close()

        database.initialize_database(legacy_path)
        records = database.list_signals(legacy_path)
        records_by_id = {record["id"]: record for record in records}
        self.assertEqual(len(records), 3)
        self.assertEqual(records_by_id[9]["original_message"], "original text")
        self.assertEqual(records_by_id[9]["created_at"], "created")
        self.assertEqual(records_by_id[9]["updated_at"], "updated")
        self.assertEqual(records_by_id[9]["channel_type"], "unknown")
        self.assertEqual(records_by_id[9]["bet_type"], "unknown")
        self.assertEqual(records_by_id[9]["signal_size_type"], "unknown")
        self.assertEqual(records_by_id[10]["original_message"], SAMPLE_SIGNAL)
        self.assertEqual(records_by_id[10]["created_at"], "created-2")
        self.assertEqual(records_by_id[10]["bet_type"], "single")
        self.assertEqual(records_by_id[10]["signal_size_type"], "regular")
        self.assertEqual(records_by_id[11]["bet_type"], "parlay")
        self.assertEqual(records_by_id[11]["signal_size_type"], "regular")
        self.assertEqual(records_by_id[11]["express_leg_count"], 3)
        self.assertEqual(records_by_id[11]["odds"], "2.09")
        self.assertEqual(records_by_id[11]["created_at"], "created-3")
        self.assertEqual(records_by_id[11]["updated_at"], "updated-3")
        self.assertEqual(len(database.list_express_legs(11, legacy_path)), 3)


def _format_rub(value):
    return format(value.quantize(Decimal("0.01")), "f")


def _print_report(report):
    overall = report["overall"]
    print("Total signals: {}".format(overall["total"]))
    print("Settled signals: {}".format(overall["settled"]))
    print("Wins: {}".format(overall["wins"]))
    print("Losses: {}".format(overall["losses"]))
    print("Voids: {}".format(overall["voids"]))
    print("Unknown results: {}".format(overall["unknown"]))
    print(
        "Settled profit: {} RUB".format(
            _format_rub(overall["settled_profit_rub"])
        )
    )
    print(
        "ROI on settled stake volume: {}%".format(
            _format_rub(overall["roi_percent"])
        )
    )
    print(
        "Potential duplicate records: {}".format(
            report["potential_duplicate_records"]
        )
    )
    print(
        "Potential conflict records: {}".format(
            report["potential_conflict_records"]
        )
    )
    if overall["wins_without_odds"]:
        print(
            "Wins excluded from profit because odds are missing: {}".format(
                overall["wins_without_odds"]
            )
        )

    for dimension, groups in report["breakdowns"].items():
        print("\nBy {}:".format(dimension.replace("_", " ")))
        if not groups:
            print("  no records")
            continue
        for key, metrics in groups.items():
            print(
                "  {}: total={}, settled={}, wins={}, losses={}, voids={}, "
                "unknown={}, settled profit={} RUB, ROI={}%".format(
                    key,
                    metrics["total"],
                    metrics["settled"],
                    metrics["wins"],
                    metrics["losses"],
                    metrics["voids"],
                    metrics["unknown"],
                    _format_rub(metrics["settled_profit_rub"]),
                    _format_rub(metrics["roi_percent"]),
                )
            )

    leg_analytics = report.get("express_leg_analytics", {})
    print("\nExpress-leg analytics (separate from main bet totals):")
    print(
        "  express bets={}, declared legs={}, parsed legs={}, "
        "leg-count mismatches={}".format(
            leg_analytics.get("express_bets", 0),
            leg_analytics.get("declared_legs", 0),
            leg_analytics.get("parsed_legs", 0),
            leg_analytics.get("bets_with_leg_count_mismatch", 0),
        )
    )
    for dimension in ("by_sport", "by_market"):
        groups = leg_analytics.get(dimension, {})
        print("  {}:".format(dimension.replace("_", " ")))
        if not groups:
            print("    no parsed legs")
        for name, count in groups.items():
            print("    {}: {} legs".format(name, count))


def main(argv=None):
    command_line = argparse.ArgumentParser(
        description="Parse and store offline betting signals."
    )
    command_line.add_argument(
        "--test",
        action="store_true",
        help="run parser and tracker tests using temporary databases",
    )
    commands = command_line.add_subparsers(dest="command")

    insert_command = commands.add_parser(
        "insert-sample", help="save the built-in sample signal"
    )
    insert_command.add_argument(
        "--channel",
        default="offline-sample",
        help="source channel name (default: offline-sample)",
    )
    insert_command.add_argument(
        "--channel-id",
        default=None,
        help="optional source channel ID",
    )
    insert_command.add_argument(
        "--message-id",
        default=None,
        help="optional message ID for duplicate protection",
    )
    insert_command.add_argument(
        "--bet-type",
        choices=("single", "parlay", "unknown"),
        default=None,
        help="override the type detected from the message header",
    )
    commands.add_parser("list", help="list saved signals and review flags")
    commands.add_parser("summary", help="show overall metrics and breakdowns")
    commands.add_parser("report", help="show overall metrics and breakdowns")
    args = command_line.parse_args(argv)

    if args.test:
        suite = unittest.TestSuite()
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ParserTests))
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(TrackerTests))
        result = unittest.TextTestRunner(verbosity=2).run(suite)
        return 0 if result.wasSuccessful() else 1

    mapping = load_channel_mapping()
    database.initialize_database()
    database.reclassify_signals(mapping)

    if args.command == "insert-sample":
        parsed = parse_signal(SAMPLE_SIGNAL)
        classification = classify_channel(
            args.channel_id, args.channel, mapping
        )
        signal_id = database.insert_signal(
            channel_name=args.channel,
            source_channel_id=args.channel_id,
            message_id=args.message_id,
            parsed_signal=parsed,
            original_message=SAMPLE_SIGNAL,
            classification=classification,
            bet_type=args.bet_type,
        )
        print(
            "Saved sample signal #{}: sport={}, channel_type={}, bet_type={}".format(
                signal_id,
                classification["sport"],
                classification["channel_type"],
                args.bet_type or parsed["bet_type"],
            )
        )
        return 0

    if args.command == "list":
        records = database.list_signals()
        if not records:
            print("No saved signals.")
            return 0
        for record in records:
            print("Signal #{}".format(record["id"]))
            for field in database.SIGNAL_FIELDS:
                value = record[field]
                print(
                    "  {}: {}".format(
                        field, "" if value is None else value
                    )
                )
            if record["bet_type"] == "parlay":
                for leg in database.list_express_legs(record["id"]):
                    print(
                        "  leg {}: {} | {} | {} ({})".format(
                            leg["leg_number"],
                            leg["sport"] or "sport unknown",
                            leg["match_event"] or "event unknown",
                            leg["selection"] or "selection unknown",
                            leg["odds"] or "odds unknown",
                        )
                    )
            print()
        return 0

    if args.command in ("summary", "report"):
        _print_report(database.build_report())
        return 0

    parsed = parse_signal(SAMPLE_SIGNAL)
    print("Parsed sample signal:")
    for field, value in parsed.items():
        print("{}: {}".format(field, value if value is not None else "not parsed"))
    missing_fields = [field for field, value in parsed.items() if value is None]
    if missing_fields:
        print("Fields that could not be parsed: {}".format(", ".join(missing_fields)))
    else:
        print("Fields that could not be parsed: none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
