from __future__ import annotations

import argparse

from sqlalchemy import select

from backend.app.core.config import settings
from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    encrypt_pii,
)
from backend.app.core.security import hash_password
from backend.app.db.database import SessionLocal
from backend.app.models import StaffUser

STAFF_DEFINITIONS = (
    {
        "email": "store_manager@halfmoon.com",
        "first_name": "Store",
        "last_name": "Manager",
        "role": "STORE_MANAGER",
        "password": settings.demo_manager_password,
    },
    {
        "email": "sales_associate@halfmoon.com",
        "first_name": "Sales",
        "last_name": "Associate",
        "role": "SALES_ASSOCIATE",
        "password": settings.demo_sales_password,
    },
)


def run_seed() -> tuple[int, int]:
    created_count = 0
    skipped_count = 0

    with SessionLocal.begin() as session:
        for staff_definition in STAFF_DEFINITIONS:
            normalized_email = staff_definition["email"].lower()
            email_lookup_hmac = create_email_lookup_hmac(normalized_email)

            existing_staff = session.scalar(
                select(StaffUser).where(
                    StaffUser.email_lookup_hmac == email_lookup_hmac
                )
            )

            if existing_staff is not None:
                skipped_count += 1
                continue

            session.add(
                StaffUser(
                    email_ciphertext=encrypt_pii(normalized_email),
                    email_lookup_hmac=email_lookup_hmac,
                    first_name_ciphertext=encrypt_pii(staff_definition["first_name"]),
                    last_name_ciphertext=encrypt_pii(staff_definition["last_name"]),
                    password_hash=hash_password(staff_definition["password"]),
                    role=staff_definition["role"],
                )
            )
            created_count += 1

    return created_count, skipped_count


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed demo staff accounts.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write staff accounts to PostgreSQL.",
    )
    args = parser.parse_args()

    if not args.apply:
        print("Dry run only. No database records were changed.")
        print(f"Staff accounts available: {len(STAFF_DEFINITIONS)}")
        return

    created_count, skipped_count = run_seed()
    print(f"Created staff accounts: {created_count}")
    print(f"Skipped existing accounts: {skipped_count}")


if __name__ == "__main__":
    main()
