from __future__ import annotations

import argparse
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.app.db.database import SessionLocal
from backend.app.models import (
    InventoryUnit,
    InventoryUnitSerialAlias,
    Product,
    ProductVariant,
)

ITALIAN_SIZES_BY_EU_SIZE = {
    "50": "10",
    "52": "12",
    "54": "14",
    "56": "16",
    "58": "18",
    "60": "20",
}
EXPECTED_MATERIALS = {"Gold", "Silver"}
EXPECTED_VARIANT_COUNT = 12
EXPECTED_UNIT_COUNT = 12_000
SERIAL_SUFFIX_PATTERN = re.compile(r"-(?:STORE|WH)-\d{6}$")


@dataclass
class VariantConversion:
    variant: ProductVariant
    old_sku: str
    new_sku: str
    italian_size: str
    units: list[InventoryUnit]


def plan_conversions(
    session: Session,
    *,
    lock_rows: bool,
) -> list[VariantConversion]:
    variant_statement = (
        select(ProductVariant)
        .join(Product, Product.id == ProductVariant.product_id)
        .where(
            Product.name == "Moon Ring",
            Product.category == "RING",
            ProductVariant.size_system == "EU",
            ProductVariant.size_label.in_(ITALIAN_SIZES_BY_EU_SIZE),
        )
        .order_by(ProductVariant.material, ProductVariant.size_label)
    )
    if lock_rows:
        variant_statement = variant_statement.with_for_update(skip_locked=True)

    variants = session.scalars(variant_statement).all()
    expected_keys = {
        (material, eu_size)
        for material in EXPECTED_MATERIALS
        for eu_size in ITALIAN_SIZES_BY_EU_SIZE
    }
    actual_keys = {(variant.material, variant.size_label) for variant in variants}

    if len(variants) != EXPECTED_VARIANT_COUNT or actual_keys != expected_keys:
        raise ValueError(
            "Expected exactly 12 EU Moon Ring variants (Gold and Silver, sizes "
            "50, 52, 54, 56, 58, and 60). No data was changed."
        )

    variant_conversions: list[VariantConversion] = []
    new_skus: list[str] = []

    for variant in variants:
        italian_size = ITALIAN_SIZES_BY_EU_SIZE[variant.size_label]
        old_sku_suffix = f"-EU{variant.size_label}"
        if not variant.sku.endswith(old_sku_suffix):
            raise ValueError(
                f"Unexpected SKU format for {variant.sku}. No data was changed."
            )

        new_sku = f"{variant.sku[:-len(old_sku_suffix)]}-{italian_size}"
        new_skus.append(new_sku)

        unit_statement = (
            select(InventoryUnit)
            .where(InventoryUnit.variant_id == variant.id)
            .order_by(InventoryUnit.serial_number)
        )
        if lock_rows:
            unit_statement = unit_statement.with_for_update(skip_locked=True)

        units = session.scalars(unit_statement).all()
        variant_conversions.append(
            VariantConversion(
                variant=variant,
                old_sku=variant.sku,
                new_sku=new_sku,
                italian_size=italian_size,
                units=units,
            )
        )

    if sum(len(conversion.units) for conversion in variant_conversions) != EXPECTED_UNIT_COUNT:
        raise ValueError(
            "Expected exactly 12,000 serialized Moon Ring units. Check the "
            "database and retry; no data was changed."
        )

    conflicting_variants = session.scalars(
        select(ProductVariant.sku).where(ProductVariant.sku.in_(new_skus))
    ).all()
    if conflicting_variants:
        raise ValueError(
            "At least one target SKU already exists. Resolve the SKU conflict "
            "before retrying; no data was changed."
        )

    planned_serials: list[str] = []
    for conversion in variant_conversions:
        old_prefix = f"{conversion.old_sku}-"
        for unit in conversion.units:
            if not unit.serial_number.startswith(old_prefix):
                raise ValueError(
                    f"Unexpected serial {unit.serial_number} for "
                    f"{conversion.old_sku}. No data was changed."
                )

            serial_suffix = unit.serial_number[len(conversion.old_sku) :]
            if not SERIAL_SUFFIX_PATTERN.fullmatch(serial_suffix):
                raise ValueError(
                    f"Unexpected serial suffix in {unit.serial_number}. "
                    "No data was changed."
                )

            planned_serials.append(f"{conversion.new_sku}{serial_suffix}")

    if len(set(planned_serials)) != EXPECTED_UNIT_COUNT:
        raise ValueError(
            "The proposed serial numbers are not unique. No data was changed."
        )

    existing_target_serials = session.scalars(
        select(InventoryUnit.serial_number).where(
            InventoryUnit.serial_number.in_(planned_serials)
        )
    ).all()
    if existing_target_serials:
        raise ValueError(
            "At least one target serial number already exists. Resolve the "
            "serial conflict before retrying; no data was changed."
        )

    old_serials = [
        unit.serial_number
        for conversion in variant_conversions
        for unit in conversion.units
    ]
    existing_aliases = session.scalars(
        select(InventoryUnitSerialAlias.old_serial_number).where(
            or_(
                InventoryUnitSerialAlias.old_serial_number.in_(old_serials),
                InventoryUnitSerialAlias.new_serial_number.in_(planned_serials),
            )
        )
    ).all()
    if existing_aliases:
        raise ValueError(
            "Serial aliases already exist for this migration. No data was changed."
        )

    return variant_conversions


def apply_conversions(
    session: Session,
    conversions: list[VariantConversion],
) -> None:
    aliases: list[InventoryUnitSerialAlias] = []

    for conversion in conversions:
        conversion.variant.size_system = None
        conversion.variant.size_label = conversion.italian_size
        conversion.variant.sku = conversion.new_sku

        for unit in conversion.units:
            old_serial_number = unit.serial_number
            new_serial_number = (
                f"{conversion.new_sku}{old_serial_number[len(conversion.old_sku):]}"
            )
            aliases.append(
                InventoryUnitSerialAlias(
                    inventory_unit_id=unit.id,
                    old_serial_number=old_serial_number,
                    new_serial_number=new_serial_number,
                )
            )
            unit.serial_number = new_serial_number

    session.add_all(aliases)


def print_plan(conversions: list[VariantConversion]) -> None:
    print("Planned Moon Ring conversions:")
    for conversion in conversions:
        print(
            f"  {conversion.old_sku} (EU {conversion.variant.size_label}) -> "
            f"{conversion.new_sku} (Italian {conversion.italian_size}); "
            f"{len(conversion.units)} serials"
        )

    status_counts = Counter(
        unit.status
        for conversion in conversions
        for unit in conversion.units
    )
    print(f"Total variants: {len(conversions)}")
    print(f"Total serials to rename and map: {sum(status_counts.values())}")
    for status, count in sorted(status_counts.items()):
        print(f"  {status}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert development Moon Ring sizes, SKUs, and serials."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the conversion; without this flag, only preview it.",
    )
    parser.add_argument(
        "--confirm-dev-inventory",
        action="store_true",
        help="Confirm the connected database contains development inventory only.",
    )
    args = parser.parse_args()

    if args.apply and not args.confirm_dev_inventory:
        parser.error(
            "--apply requires --confirm-dev-inventory after verifying the "
            "database connection."
        )

    if args.apply:
        with SessionLocal.begin() as session:
            conversions = plan_conversions(session, lock_rows=True)
            print_plan(conversions)
            apply_conversions(session, conversions)
        print("Conversion completed.")
        return

    with SessionLocal() as session:
        conversions = plan_conversions(session, lock_rows=False)
        print_plan(conversions)
    print("Preview only. No database records were changed.")


if __name__ == "__main__":
    main()
