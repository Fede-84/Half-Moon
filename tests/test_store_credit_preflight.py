from argparse import Namespace

from sqlalchemy.engine import make_url

from scripts.preflight_store_credit_retirement import _is_expected_url


def test_preflight_accepts_only_the_explicit_remote_identity() -> None:
    url = make_url(
        "postgresql+psycopg://readonly@production-db.example/half_moon_prod"
    )

    assert _is_expected_url(
        url,
        Namespace(
            expected_host="production-db.example",
            expected_database="half_moon_prod",
            expected_user="readonly",
        ),
    )


def test_preflight_refuses_local_or_test_targets(capsys) -> None:
    url = make_url(
        "postgresql+psycopg://half_moon_test_user@127.0.0.1:55432/"
        "half_moon_test_db"
    )

    assert not _is_expected_url(
        url,
        Namespace(
            expected_host="127.0.0.1",
            expected_database="half_moon_test_db",
            expected_user="half_moon_test_user",
        ),
    )
    assert "local or test database" in capsys.readouterr().err
