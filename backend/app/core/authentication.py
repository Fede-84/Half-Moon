from uuid import UUID

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from backend.app.core.security import decode_access_token
from backend.app.db.database import get_db
from backend.app.models.customer import Customer
from backend.app.models.customer_account import CustomerAccount
from backend.app.models.staff_user import StaffUser

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


def _decode_token_identity(
    credentials: HTTPAuthorizationCredentials | None,
    detail: str,
) -> tuple[UUID, dict[str, object]]:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        payload = decode_access_token(credentials.credentials)
        subject = payload.get("sub")

        if not isinstance(subject, str):
            raise TypeError("Token subject is invalid")

        return UUID(subject), payload
    except ValueError, KeyError, TypeError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=detail,
            headers={"WWW-Authenticate": "Bearer"},
        ) from None


def get_current_customer(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),  # noqa: B008
) -> Customer:
    customer_id, _ = _decode_token_identity(
        credentials,
        "Invalid authentication credentials",
    )

    customer = (
        db.query(Customer)
        .join(CustomerAccount, CustomerAccount.customer_id == Customer.id)
        .filter(
            Customer.id == customer_id,
            CustomerAccount.account_status == "ACTIVE",
        )
        .first()
    )

    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return customer


def get_current_staff(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),  # noqa: B008
) -> StaffUser:
    staff_id, payload = _decode_token_identity(
        credentials,
        "Invalid staff authentication credentials",
    )
    token_role = payload.get("role")

    if not isinstance(token_role, str):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid staff authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

    staff_user = db.query(StaffUser).filter(StaffUser.id == staff_id).first()

    if staff_user is None or staff_user.account_status != "ACTIVE":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Staff account is not active",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if staff_user.role != token_role:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Staff role is invalid",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return staff_user


def require_staff_role(required_role: str):
    def role_checker(
        staff_user: StaffUser = Depends(get_current_staff),  # noqa: B008
    ) -> StaffUser:
        if staff_user.role != required_role:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient staff permissions",
            )

        return staff_user

    return role_checker
