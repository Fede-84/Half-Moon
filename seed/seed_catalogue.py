from __future__ import annotations

import argparse
import csv
import re
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.db.database import SessionLocal
from backend.app.models import (
    InventoryUnit,
    Location,
    Product,
    ProductVariant,
)

CATALOGUE_PATH = Path(__file__).with_name("catalogue_preview.csv")

LOCATION_DEFINITIONS = (
    ("PHYSICAL_STORE", "Physical Store", "STORE", "STORE"),
    ("MAIN_WAREHOUSE", "Main Warehouse", "WAREHOUSE", "WH"),
)

ITALIAN_TO_EU_RING_SIZE = {
    "10": "50",
    "12": "52",
    "14": "54",
    "16": "56",
    "18": "58",
    "20": "60",
}

REQUIRED_COLUMNS = {
    "product_name",
    "category",
    "description",
    "material",
    "color",
    "size_system",
    "size_label",
    "sku",
    "ean",
    "retail_price",
    "image_url",
    "units_per_location",
}


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    return value.strip("-")


def load_catalogue_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        rows = [{key: value or "" for key, value in row.items()} for row in reader]

    if not rows:
        raise ValueError("Catalogue CSV contains no data rows.")

    missing_columns = REQUIRED_COLUMNS - set(rows[0])
    if missing_columns:
        missing = ", ".join(sorted(missing_columns))
        raise ValueError(f"Catalogue CSV is missing columns: {missing}")

    return rows


def validate_catalogue_rows(rows: list[dict[str, str]]) -> None:
    seen_skus: set[str] = set()
    seen_eans: set[str] = set()

    for line_number, row in enumerate(rows, start=2):
        sku = row["sku"]
        ean = row["ean"]

        if sku in seen_skus:
            raise ValueError(f"Duplicate SKU on CSV line {line_number}: {sku}")

        if ean in seen_eans:
            raise ValueError(f"Duplicate EAN on CSV line {line_number}: {ean}")

        if not re.fullmatch(r"\d{13}", ean):
            raise ValueError(
                f"EAN must contain exactly 13 digits on CSV line {line_number}: {ean}"
            )

        retail_price = Decimal(row["retail_price"])
        units_per_location = int(row["units_per_location"])

        if retail_price < 0:
            raise ValueError(f"Retail price cannot be negative: {sku}")

        if units_per_location < 0:
            raise ValueError(f"Units cannot be negative: {sku}")

        seen_skus.add(sku)
        seen_eans.add(ean)


def get_or_create_locations(session: Session) -> dict[str, Location]:
    locations: dict[str, Location] = {}

    for code, name, location_type, _ in LOCATION_DEFINITIONS:
        location = session.scalar(select(Location).where(Location.code == code))

        if location is None:
            location = Location(
                code=code,
                name=name,
                location_type=location_type,
            )
            session.add(location)
            session.flush()

        locations[code] = location

    return locations


def get_or_create_product(
    session: Session,
    row: dict[str, str],
) -> Product:
    slug = slugify(row["product_name"])

    product = session.scalar(select(Product).where(Product.slug == slug))

    if product is None:
        product = Product(
            name=row["product_name"],
            slug=slug,
            description=row["description"],
            category=row["category"],
            image_url=row["image_url"],
        )
        session.add(product)
        session.flush()

    return product


def get_or_create_variant(
    session: Session,
    row: dict[str, str],
    product: Product,
) -> ProductVariant:
    variant = session.scalar(
        select(ProductVariant).where(ProductVariant.sku == row["sku"])
    )

    if (
        variant is None
        and row["category"] == "RING"
        and not row["size_system"]
        and row["size_label"] in ITALIAN_TO_EU_RING_SIZE
    ):
        legacy_variant = session.scalar(
            select(ProductVariant).where(
                ProductVariant.product_id == product.id,
                ProductVariant.material == row["material"],
                ProductVariant.color == row["color"],
                ProductVariant.size_system == "EU",
                ProductVariant.size_label
                == ITALIAN_TO_EU_RING_SIZE[row["size_label"]],
            )
        )
        if legacy_variant is not None:
            raise ValueError(
                "Legacy EU ring variants are still present. Run "
                "migrate_ring_sizes_to_italian before reseeding."
            )

    if variant is None:
        variant = ProductVariant(
            product_id=product.id,
            material=row["material"],
            color=row["color"],
            size_system=row["size_system"] or None,
            size_label=row["size_label"],
            sku=row["sku"],
            ean=row["ean"],
            retail_price=Decimal(row["retail_price"]),
            image_url=row["image_url"],
        )
        session.add(variant)
        session.flush()

    return variant


def create_inventory_units(
    session: Session,
    row: dict[str, str],
    variant: ProductVariant,
    locations: dict[str, Location],
) -> int:
    units_per_location = int(row["units_per_location"])
    units_created = 0
    variant_serials = set(
        session.scalars(
            select(InventoryUnit.serial_number).where(
                InventoryUnit.variant_id == variant.id
            )
        )
    )
    if any(
        not serial_number.startswith(f"{variant.sku}-")
        for serial_number in variant_serials
    ):
        raise ValueError(
            f"Inventory serials for {variant.sku} have not been migrated. "
            "Run migrate_ring_sizes_to_italian before reseeding."
        )

    for location_code, _, _, short_code in LOCATION_DEFINITIONS:
        location = locations[location_code]
        serial_prefix = f"{row['sku']}-{short_code}-"

        existing_serials = set(
            session.scalars(
                select(InventoryUnit.serial_number).where(
                    InventoryUnit.serial_number.like(f"{serial_prefix}%")
                )
            )
        )

        for sequence in range(1, units_per_location + 1):
            serial_number = f"{serial_prefix}{sequence:06d}"

            if serial_number in existing_serials:
                continue

            session.add(
                InventoryUnit(
                    variant_id=variant.id,
                    serial_number=serial_number,
                    location_id=location.id,
                    status="IN_STOCK",
                )
            )
            units_created += 1

    return units_created


def run_seed(rows: list[dict[str, str]]) -> int:
    units_created = 0

    with SessionLocal.begin() as session:
        locations = get_or_create_locations(session)

        for row in rows:
            product = get_or_create_product(session, row)
            variant = get_or_create_variant(session, row, product)
            units_created += create_inventory_units(
                session,
                row,
                variant,
                locations,
            )

    return units_created


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Seed Half Moon catalogue and inventory data."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write catalogue and inventory data to PostgreSQL.",
    )
    args = parser.parse_args()

    rows = load_catalogue_rows(CATALOGUE_PATH)
    validate_catalogue_rows(rows)

    expected_units = sum(
        int(row["units_per_location"]) * len(LOCATION_DEFINITIONS) for row in rows
    )

    print(f"Validated {len(rows)} catalogue variants.")
    print(f"Expected inventory units: {expected_units}")

    if not args.apply:
        print("Dry run only. No database records were changed.")
        return

    units_created = run_seed(rows)
    print(f"Created {units_created} new inventory units.")


if __name__ == "__main__":
    main()
