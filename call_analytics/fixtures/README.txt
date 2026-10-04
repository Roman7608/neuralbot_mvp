classify_golden_cases.json — эталонные транскрипты и ожидаемая пара (department, call_type).
Запуск: python -m unittest call_analytics.test_classify_golden -v

Поле xfail_until_refactor: true — кейс ещё не совпадает с целевым результатом; тест пропускает строгую проверку.
Когда классификатор начнёт выдавать expected_* — удалите xfail_until_refactor (тест упадёт с подсказкой, если забыть).
