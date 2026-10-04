"""Сопоставление manager_name из БД со строками матрицы ОП."""
import unittest

from admin_panel.managers_config import (
    OP_AMBIGUOUS_ANDREY_LABEL,
    canonical_op_manager_for_db,
    get_op_manager_matrix_labels,
    resolve_op_manager_roster_label,
    stored_manager_to_display,
)
from analyze_call_quality import (
    _extract_salon_admin_manager_name,
    extract_manager_name_from_full_transcript,
)


class TestResolveOpManagerRosterLabel(unittest.TestCase):
    def test_matrix_labels_count(self):
        labels = get_op_manager_matrix_labels()
        self.assertEqual(len(labels), 6)
        self.assertIn(OP_AMBIGUOUS_ANDREY_LABEL, labels)

    def test_reversed_name_order(self):
        self.assertEqual(resolve_op_manager_roster_label("Андрей Калаев"), "Калаев Андрей")
        self.assertEqual(
            resolve_op_manager_roster_label("Анастасия Краснощекова"),
            "Краснощекова Анастасия",
        )
        self.assertEqual(
            resolve_op_manager_roster_label("Евгений Евдокимов"),
            "Евдокимов Евгений",
        )

    def test_firstname_only_anastasia(self):
        self.assertEqual(resolve_op_manager_roster_label("Анастасия"), "Краснощекова Анастасия")

    def test_firstname_only_andrey_ambiguous(self):
        self.assertEqual(resolve_op_manager_roster_label("Андрей"), OP_AMBIGUOUS_ANDREY_LABEL)
        self.assertEqual(
            resolve_op_manager_roster_label("Андрей (неясно, Калаев или Щеголев)"),
            OP_AMBIGUOUS_ANDREY_LABEL,
        )

    def test_canonical_for_db(self):
        self.assertEqual(canonical_op_manager_for_db("Андрей Калаев"), "Калаев Андрей")
        self.assertEqual(canonical_op_manager_for_db("Андрей"), "Андрей")

    def test_salon_admin_not_op_manager_display(self):
        t = "Администратор салона Анастасия, здравствуйте. Переведу на кузовной отдел."
        self.assertEqual(_extract_salon_admin_manager_name(t), "Администратор Анастасия")
        self.assertEqual(stored_manager_to_display("Анастасия", department="OTHER"), "Анастасия")
        self.assertNotEqual(
            stored_manager_to_display("Анастасия", department="OTHER"),
            "Краснощекова Анастасия",
        )
        self.assertEqual(
            stored_manager_to_display("Администратор Анастасия", department="OTHER"),
            "Администратор Анастасия",
        )
        self.assertEqual(
            stored_manager_to_display("Анастасия", department="OP"),
            "Краснощекова Анастасия",
        )

    def test_call_11226_manager_sergey_after_last_handover(self):
        """Админ Анастасия → перевод → «Сергей, слушаю вас» — менеджер Сергей, не Краснощекова."""
        t = (
            "Администратор салона Анастасия, здравствуйте. "
            "Я сейчас переведу вас на кружевной отдел. Оставайтесь на линии. "
            "Викингузвно Сергей, слушаю вас. Здравствуйте."
        )
        self.assertEqual(extract_manager_name_from_full_transcript(t, "OTHER"), "Сергей")
        self.assertEqual(_extract_salon_admin_manager_name(t), "Администратор Анастасия")

    def test_call_18543_name_before_maspemshchik_role_is_vladimir(self):
        """18543: «Владимир, маспёмщик, слушаю вас» — имя из справочника, роль отбрасывается."""
        t = (
            "Официальный дилер Чери и Тенет Викинги на Заставной, "
            "администратор Диана. Добрый день! "
            "Я у вас седьмого числа был на ремонт колеса. "
            "Переведу вас на специалиста. Оставайтесь на линии. "
            "Владимир, маспёмщик, слушаю вас. Здравствуйте."
        )
        self.assertEqual(
            extract_manager_name_from_full_transcript(t, "OTHER"),
            "Владимир",
        )

    def test_call_19212_mastpriemshchik_role_is_not_manager_name(self):
        """19212: «Владимир, Мастприёмщик, слушаю вас» — менеджер Владимир, не роль."""
        from analyze_call_quality import (
            _is_stt_junk_manager_name_candidate,
            _sanitize_extracted_manager_display,
        )

        for role in ("Мастприёмщик", "Мастерприемщик", "МАСТЕРПРИЕМЩИК", "маспёмщик"):
            with self.subTest(role=role):
                self.assertTrue(_is_stt_junk_manager_name_candidate(role), role)
                self.assertIsNone(_sanitize_extracted_manager_display(role), role)
        t = (
            "ичеервис Андреева Юли. Здравствуйте. Здравствуйте, компания ТНх беспокоит. "
            "Так, хорошо, я вас сейчас на Владимира та переведу. "
            "Э что мастер-переводчик, который занимается вашим автомобилем. "
            "Пожалуйста, на линии, ожидайте, минутку. "
            "Волод. Владимир, Мастприёмщик, слушаю вас. "
            "Да, Владимир, добрый день."
        )
        self.assertEqual(
            extract_manager_name_from_full_transcript(t, "OTHER"),
            "Владимир",
        )

    def test_call_13630_avito_soedinyayem_manager_andrey_not_diana(self):
        """Авито: «Соединяем?» / «соединяйте» — handover; менеджер Андрей, не админ Диана."""
        t = (
            "Официальный дилер Chrт, администратор Диана. Добрый день! "
            "Здравствуйте, звонок из компании АВИТО, на линии клиент Максим. "
            "Рассматриваю для себ Tenet T7 как физическое лицо. "
            "Соединяем Максим? Да, соединяйте. Благодарю. "
            "Благодарюобрый день, официальный дилер чене Тольятти Викинги. "
            "Меня зовут Андрей. Меня зовут Андрей, Ввас Максим, правильно? "
            "Максим, Андрей, компания Викинги Тольятти."
        )
        self.assertEqual(extract_manager_name_from_full_transcript(t, department="OP"), "Андрей")
        self.assertEqual(_extract_salon_admin_manager_name(t), "Администратор Диана")

    def test_call_13800_admin_intake_anton_manager_andrey_not_anton(self):
        """Админ уточняет имя клиента Антон; менеджер — Андрей («меня зовут Андрей, вас Антон»)."""
        t = (
            "Официальный дилер ChariTnt, администратор Диана. Добрый день. Здравствуйте. "
            "как я просил в отдел продаж. Какой автомобиль интересует? Те Tenet T7. "
            "Подскажите ваше имя? Антон. Антон, переведу вас. на менеджер отдела продаж. "
            "Оставайтесь на линии. Переводите. Алло. Добрый день, я официальный дилер "
            "рачеерне Тольятти, коомпания Викинги. меня зовут Андрей, вас Антон, правильно? "
            "Да-да. Викинги же? Да, Викинги, Заставной улице, да."
        )
        self.assertEqual(extract_manager_name_from_full_transcript(t, department="OP"), "Андрей")
        self.assertEqual(_extract_salon_admin_manager_name(t), "Администратор Диана")

    def test_call_27458_client_menya_zovut_anton_manager_andrey(self):
        """27458: первое «Меня зовут Антон» — клиент, менеджер далее «Андрей, менеджер отдела продаж»."""
        t = (
            "это ко мне,ы.. Мы уже общались. Меня зовут Антон. Здравствуйте. "
            "Алло. Екатерина, здравствуйте. Андрей, менеджер отдела продаж Tenet Тольятти-Викинги."
        )
        self.assertEqual(
            extract_manager_name_from_full_transcript(t, department="OP"),
            "Андрей",
        )

    def test_call_13761_kuzov_master_pravel_stt_to_pavel(self):
        """Кузовной: «мастер-приёмщик Правел» (STT) → Павел Алексеев; не клиентка Елена."""
        t = (
            "Оять. . Алло. Елена? Да. День добрый, это Викинги кузовной, мастер-приёмщик Правел. "
            "Удобно? У вас там какие-то вопросы, я так понимаю, по поводу кузова, что-то там, да? "
            "Да, записывайте меня на осмотр. На осмотр. Смотрите, могу предложить 25-е число. "
            "Всё хорошо, спасибо, Павел."
        )
        mgr = extract_manager_name_from_full_transcript(t, department="OTHER")
        self.assertEqual(mgr, "Павел Алексеев")
        self.assertNotEqual(mgr, "Елена")
        self.assertNotEqual(mgr, "Правел")

    def test_18454_polina_official_dealer_intro_as_salon_admin(self):
        """18454: «официальный дилер… меня зовут Полина» → Администратор Полина."""
        t = (
            "Официальный дилер бренда Тэнет, меня зовут Полина. Добрый день. "
            "Здравствуйте, я хотела бы на ТО записаться плановый. "
            "У нас сервис не работает в субботу-воскресенье. К сожалению, нет, диспетчеров нету."
        )
        self.assertEqual(_extract_salon_admin_manager_name(t), "Администратор Полина")


if __name__ == "__main__":
    unittest.main()
