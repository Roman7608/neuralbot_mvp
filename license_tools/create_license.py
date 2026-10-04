#!/usr/bin/env python3
"""
Создание файла лицензии (JWT RS256) для проекта Vikingi.

Использование:
    python create_license.py --client vikingi-auto --months 7 --private-key private_key.pem

Результат: файл license.key (JWT-токен).
"""

import argparse
import json
import base64
from pathlib import Path
from datetime import date, timedelta
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def create_jwt(payload: dict, private_key_pem: bytes) -> str:
    """Создать JWT RS256 токен."""
    header = {"alg": "RS256", "typ": "JWT"}
    header_b = _b64encode(json.dumps(header, separators=(",", ":")).encode())
    payload_b = _b64encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode())
    message = f"{header_b}.{payload_b}".encode("ascii")

    private_key = serialization.load_pem_private_key(private_key_pem, password=None)
    signature = private_key.sign(message, padding.PKCS1v15(), hashes.SHA256())
    sig_b = _b64encode(signature)

    return f"{header_b}.{payload_b}.{sig_b}"


def main():
    parser = argparse.ArgumentParser(description="Создание лицензии Vikingi")
    parser.add_argument("--client", required=True, help="Имя клиента (например: vikingi-auto)")
    parser.add_argument("--months", type=int, required=True, help="Срок лицензии в месяцах")
    parser.add_argument("--private-key", type=Path, required=True, help="Путь к private_key.pem")
    parser.add_argument("--modules", default="admin_panel,telegram_bot,voice_bot,stt,tts",
                        help="Модули через запятую (по умолчанию: все)")
    parser.add_argument("--output", type=Path, default=Path("license.key"), help="Выходной файл")
    args = parser.parse_args()

    if not args.private_key.exists():
        print(f"Ошибка: файл {args.private_key} не найден")
        return

    today = date.today()
    valid_until = today + timedelta(days=args.months * 30)

    payload = {
        "client": args.client,
        "issued_at": today.isoformat(),
        "valid_until": valid_until.isoformat(),
        "modules": [m.strip() for m in args.modules.split(",")],
    }

    private_key_pem = args.private_key.read_bytes()
    token = create_jwt(payload, private_key_pem)
    args.output.write_text(token)

    print(f"Лицензия создана: {args.output}")
    print(f"  Клиент:    {payload['client']}")
    print(f"  Выдана:    {payload['issued_at']}")
    print(f"  Истекает:  {payload['valid_until']}")
    print(f"  Модули:    {', '.join(payload['modules'])}")
    print(f"  Срок:      {args.months} мес ({args.months * 30} дней)")


if __name__ == "__main__":
    main()
