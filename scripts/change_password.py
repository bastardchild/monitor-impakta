#!/usr/bin/env python3
"""
Script praktis untuk mengganti password Admin pada Monitor Impakta.
Dapat dijalankan langsung di host atau via docker exec:
  docker exec -it monitor_impakta python scripts/change_password.py
"""
import getpass
import os
import sys
import bcrypt


def main():
    print("=" * 55)
    print("  Ganti Password Admin - Monitor Impakta")
    print("=" * 55)

    if len(sys.argv) > 1:
        password = sys.argv[1]
    else:
        try:
            password = getpass.getpass("Masukkan Password Baru: ")
            confirm = getpass.getpass("Konfirmasi Password Baru: ")
            if password != confirm:
                print("Error: Konfirmasi password tidak cocok!")
                sys.exit(1)
        except Exception:
            print("Error: Tidak dapat membaca input password.")
            sys.exit(1)

    if len(password) < 6:
        print("Error: Password minimal 6 karakter!")
        sys.exit(1)

    salt = bcrypt.gensalt(rounds=12)
    raw_hash = bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")
    env_hash = raw_hash.replace("$", "$$")

    print("\n[OK] Hash password berhasil dibuat:")
    print(f"ADMIN_PASSWORD_HASH={env_hash}")

    # Cari file .env untuk update otomatis
    candidate_paths = [
        ".env",
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        "/app/.env"
    ]
    target_env = None
    for p in candidate_paths:
        if os.path.exists(p) and os.path.isfile(p):
            target_env = os.path.abspath(p)
            break

    if target_env:
        try:
            with open(target_env, "r", encoding="utf-8") as f:
                lines = f.readlines()

            updated = False
            new_lines = []
            for line in lines:
                if line.strip().startswith("ADMIN_PASSWORD_HASH="):
                    new_lines.append(f"ADMIN_PASSWORD_HASH={env_hash}\n")
                    updated = True
                else:
                    new_lines.append(line)

            if not updated:
                new_lines.append(f"\nADMIN_PASSWORD_HASH={env_hash}\n")

            with open(target_env, "w", encoding="utf-8") as f:
                f.writelines(new_lines)

            print(f"\n[SUKSES] File .env ({target_env}) berhasil diperbarui otomatis!")
            print("Langkah terakhir, restart container agar password baru aktif:")
            print("  docker restart monitor_impakta")
        except Exception as e:
            print(f"\n[INFO] Gagal menulis ke .env ({e}).")
            print("Silakan buka file .env secara manual dan ganti baris:")
            print(f"ADMIN_PASSWORD_HASH={env_hash}")
    else:
        print("\nSilakan salin baris di atas dan perbarui ke file .env Anda, lalu restart container.")


if __name__ == "__main__":
    main()
