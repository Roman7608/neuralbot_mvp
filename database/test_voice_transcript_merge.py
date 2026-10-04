"""Объединение последовательных частей одной реплики голосового бота."""

import unittest
from datetime import datetime, timedelta

from database.postgresql_manager import _merge_consecutive_bot_transcript_turns


class VoiceTranscriptMergeTest(unittest.TestCase):
    def test_merges_bot_parts_until_client_turn(self) -> None:
        started = datetime(2026, 7, 15, 8, 27, 4)
        turns = [
            {
                "seq": 3,
                "role": "bot",
                "text": "Поняла.",
                "logged_at": started,
            },
            {
                "seq": 4,
                "role": "bot",
                "text": "Я могу записать Вас сама или перевести на ассистента.",
                "logged_at": started + timedelta(seconds=4),
            },
            {
                "seq": 5,
                "role": "client",
                "text": "Ассистент сервиса.",
                "logged_at": started + timedelta(seconds=8),
            },
        ]

        merged = _merge_consecutive_bot_transcript_turns(turns)

        self.assertEqual(len(merged), 2)
        self.assertEqual(
            merged[0]["text"],
            "Поняла. Я могу записать Вас сама или перевести на ассистента.",
        )
        self.assertEqual(merged[0]["seq"], 3)
        self.assertEqual(merged[0]["logged_at"], started)
        self.assertEqual(merged[1]["role"], "client")

    def test_keeps_separate_bot_turns_after_client_reply(self) -> None:
        turns = [
            {"seq": 1, "role": "bot", "text": "Первый вопрос."},
            {"seq": 2, "role": "client", "text": "Ответ."},
            {"seq": 3, "role": "bot", "text": "Второй вопрос."},
        ]

        self.assertEqual(_merge_consecutive_bot_transcript_turns(turns), turns)

    def test_empty_bot_part_does_not_add_spaces(self) -> None:
        turns = [
            {"seq": 1, "role": "bot", "text": ""},
            {"seq": 2, "role": "bot", "text": "Текст."},
        ]

        merged = _merge_consecutive_bot_transcript_turns(turns)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["text"], "Текст.")

    def test_does_not_mutate_source_turns(self) -> None:
        turns = [
            {"seq": 1, "role": "bot", "text": "Поняла."},
            {"seq": 2, "role": "bot", "text": "Продолжение."},
        ]

        _merge_consecutive_bot_transcript_turns(turns)

        self.assertEqual(turns[0]["text"], "Поняла.")


if __name__ == "__main__":
    unittest.main()
