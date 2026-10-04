#!/usr/bin/env python3
"""
Генерация пары RSA-4096 ключей для лицензирования Vikingi.
Запускать ОДИН раз. Приватный ключ хранить в безопасном месте.

Использование:
    python generate_keys.py [--out-dir .]
"""

import argparse
from pathlib import Path
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization


def generate(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=4096)

    priv_path = out_dir / "private_key.pem"
    priv_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    print(f"Приватный ключ: {priv_path}")
    print("  ⚠ ХРАНИТЬ ТОЛЬКО У ВАС. Никогда не передавать заказчику.")

    pub_path = out_dir / "public_key.pem"
    pub_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    print(f"Публичный ключ: {pub_path}")
    print("  Этот файл копируется на сервер в licensing/public_key.pem")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Генерация RSA-ключей для лицензии Vikingi")
    parser.add_argument("--out-dir", type=Path, default=Path(__file__).parent, help="Каталог для сохранения ключей")
    args = parser.parse_args()
    generate(args.out_dir)
