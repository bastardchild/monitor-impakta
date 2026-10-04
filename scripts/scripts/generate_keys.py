#!/usr/bin/env python3
import getpass
import secrets
import sys
import bcrypt
from cryptography.fernet import Fernet


def main():
    print("=" * 60)
    print(" Social Media KPI Dashboard - Key & Credential Generator")
    print("=" * 60)
    
    # Generate cryptographic keys
    token_encryption_key = Fernet.generate_key().decode("utf-8")
    app_secret_key = secrets.token_urlsafe(32)
    ingest_api_key = secrets.token_urlsafe(32)
    n8n_encryption_key = secrets.token_urlsafe(32)
    n8n_webhook_secret = secrets.token_urlsafe(32)

    # Prompt for admin password
    print("\nMasukkan password untuk user Admin:")
    try:
        # Check if stdin is a tty
        if sys.stdin.isatty():
            password = getpass.getpass("Kata Sandi Admin: ")
            password_confirm = getpass.getpass("Konfirmasi Kata Sandi Admin: ")
            if password != password_confirm:
                print("Error: Konfirmasi kata sandi tidak cocok!")
                sys.exit(1)
        else:
            # Piped or non-interactive fallback
            line = sys.stdin.readline().strip()
            password = line if line else "AdminSecret123!"
            print("(Menggunakan input non-interaktif)")
    except Exception:
        password = "AdminSecret123!"

    salt = bcrypt.gensalt(rounds=12)
    admin_password_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")

    print("\n" + "-" * 60)
    print("Salin nilai-nilai berikut ke file .env Anda:")
    print("-" * 60)
    print(f"TOKEN_ENCRYPTION_KEY={token_encryption_key}")
    print(f"APP_SECRET_KEY={app_secret_key}")
    print(f"INGEST_API_KEY={ingest_api_key}")
    print(f"N8N_ENCRYPTION_KEY={n8n_encryption_key}")
    print(f"N8N_WEBHOOK_SECRET={n8n_webhook_secret}")
    print(f"ADMIN_PASSWORD_HASH={admin_password_hash}")
    print("-" * 60)
    print("Selesai! Jangan membagikan kunci rahasia ini ke repository.")


if __name__ == "__main__":
    main()
