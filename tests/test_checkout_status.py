from decimal import Decimal
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.models.order import Order
from backend.app.models.payment import Payment


def make_checkout_order(
    test_db_session: Session,
    checkout_session_id: str,
) -> Order:
    order = Order(
        order_number=f"HM-TEST-{uuid4().hex[:12]}",
        channel="ECOM",
        fulfillment_method="SHIPPING",
        order_status="PENDING_PAYMENT",
        payment_status="UNPAID",
        currency="EUR",
        subtotal_gross=Decimal("25.00"),
        discount_total_gross=Decimal("0.00"),
        taxable_gross=Decimal("25.00"),
        net_total=Decimal("20.49"),
        vat_total=Decimal("4.51"),
        total_gross=Decimal("25.00"),
        standard_vat_rate=Decimal("22.00"),
        applied_vat_rate=Decimal("22.00"),
    )
    test_db_session.add(order)
    test_db_session.flush()
    test_db_session.add(
        Payment(
            order_id=order.id,
            payment_method="CARD",
            provider="STRIPE",
            provider_checkout_session_id=checkout_session_id,
            amount=Decimal("25.00"),
            currency="EUR",
            status="PENDING",
        )
    )
    test_db_session.flush()
    return order


def test_checkout_status_reports_webhook_persisted_payment_state(
    api_client,
    test_db_session,
) -> None:
    checkout_session_id = f"cs_test_{uuid4().hex}"
    order = make_checkout_order(test_db_session, checkout_session_id)

    pending_response = api_client.get(
        "/api/v1/checkout/status",
        params={"session_id": checkout_session_id},
    )

    assert pending_response.status_code == 200
    assert pending_response.json() == {
        "order_status": "PENDING_PAYMENT",
        "payment_status": "UNPAID",
    }

    order.order_status = "PAID"
    order.payment_status = "PAID"
    payment = (
        test_db_session.query(Payment)
        .filter(Payment.provider_checkout_session_id == checkout_session_id)
        .one()
    )
    payment.status = "PAID"
    test_db_session.flush()

    paid_response = api_client.get(
        "/api/v1/checkout/status",
        params={"session_id": checkout_session_id},
    )

    assert paid_response.status_code == 200
    assert paid_response.json() == {
        "order_status": "PAID",
        "payment_status": "PAID",
    }


def test_checkout_status_does_not_return_order_details_for_unknown_session(
    api_client,
) -> None:
    response = api_client.get(
        "/api/v1/checkout/status",
        params={"session_id": f"cs_test_{uuid4().hex}"},
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Checkout status was not found"}
