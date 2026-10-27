from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from backend.app.core.data_protection import (
    create_email_lookup_hmac,
    encrypt_pii,
)
from backend.app.models.customer import Customer


def normalize_email(email: str) -> str:
    return email.strip().lower()


def find_customer_by_email(
    db: Session,
    email: str,
) -> Customer | None:
    normalized_email = normalize_email(email)
    email_lookup_hmac = create_email_lookup_hmac(normalized_email)

    return (
        db.query(Customer)
        .filter(Customer.email_lookup_hmac == email_lookup_hmac)
        .first()
    )


def get_or_create_customer(
    db: Session,
    email: str,
    first_name: str | None = None,
    last_name: str | None = None,
) -> Customer:
    normalized_email = normalize_email(email)
    email_lookup_hmac = create_email_lookup_hmac(normalized_email)

    statement = (
        postgresql_insert(Customer)
        .values(
            email_ciphertext=encrypt_pii(normalized_email),
            email_lookup_hmac=email_lookup_hmac,
            first_name_ciphertext=(
                encrypt_pii(first_name.strip())
                if first_name and first_name.strip()
                else None
            ),
            last_name_ciphertext=(
                encrypt_pii(last_name.strip())
                if last_name and last_name.strip()
                else None
            ),
        )
        .on_conflict_do_nothing(
            index_elements=[Customer.email_lookup_hmac],
        )
    )

    db.execute(statement)

    return (
        db.query(Customer)
        .filter(Customer.email_lookup_hmac == email_lookup_hmac)
        .with_for_update()
        .one()
    )


def update_customer_profile(
    customer: Customer,
    *,
    first_name: str | None = None,
    last_name: str | None = None,
    phone: str | None = None,
) -> None:
    if first_name is not None and first_name.strip():
        customer.first_name_ciphertext = encrypt_pii(first_name.strip())

    if last_name is not None and last_name.strip():
        customer.last_name_ciphertext = encrypt_pii(last_name.strip())

    if phone is not None and phone.strip():
        customer.phone_ciphertext = encrypt_pii(phone.strip())
