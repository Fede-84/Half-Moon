from uuid import UUID

from sqlalchemy.orm import Session

from backend.app.db.database import commit_or_rollback, flush_or_rollback
from backend.app.models.inventory_unit import InventoryUnit
from backend.app.models.location import Location
from backend.app.models.product import Product
from backend.app.models.product_variant import ProductVariant
from backend.app.services.reservations import release_expired_reservations


def _serialize_variant(variant: ProductVariant) -> dict[str, object]:
    return {
        "id": str(variant.id),
        "product_id": str(variant.product_id),
        "material": variant.material,
        "color": variant.color,
        "size_system": variant.size_system,
        "size_label": variant.size_label,
        "sku": variant.sku,
        "ean": variant.ean,
        "retail_price": str(variant.retail_price),
        "image_url": variant.image_url,
    }


def _get_location_totals(
    db: Session,
    variant_id: UUID,
) -> dict[str, int]:
    inventory_rows = (
        db.query(InventoryUnit, Location)
        .join(Location, InventoryUnit.location_id == Location.id, isouter=True)
        .filter(
            InventoryUnit.variant_id == variant_id,
            InventoryUnit.status == "IN_STOCK",
        )
        .all()
    )

    location_totals: dict[str, int] = {}
    for _, location in inventory_rows:
        location_code = location.code if location else "UNKNOWN"
        location_totals[location_code] = location_totals.get(location_code, 0) + 1

    return location_totals


def list_catalog_products(db: Session) -> list[dict[str, object]]:
    products = db.query(Product).all()
    if not products:
        return []

    product_ids = [product.id for product in products]
    variants_by_product: dict[UUID, list[dict[str, object]]] = {
        product_id: [] for product_id in product_ids
    }

    variants = (
        db.query(ProductVariant)
        .filter(ProductVariant.product_id.in_(product_ids))
        .all()
    )
    for variant in variants:
        variants_by_product[variant.product_id].append(
            {
                "id": str(variant.id),
                "color": variant.color,
                "size_system": variant.size_system,
                "size_label": variant.size_label,
                "retail_price": str(variant.retail_price),
                "image_url": variant.image_url,
            }
        )

    return [
        {
            "id": str(product.id),
            "name": product.name,
            "slug": product.slug,
            "description": product.description,
            "category": product.category,
            "image_url": product.image_url,
            "variants": variants_by_product[product.id],
        }
        for product in products
    ]


def get_product_payload(
    db: Session,
    product_id: UUID,
) -> dict[str, object]:
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise ValueError("Product not found")

    variants = (
        db.query(ProductVariant).filter(ProductVariant.product_id == product_id).all()
    )

    return {
        "id": str(product.id),
        "name": product.name,
        "slug": product.slug,
        "description": product.description,
        "category": product.category,
        "variants": [_serialize_variant(variant) for variant in variants],
    }


def get_product_variants_payload(
    db: Session,
    product_id: UUID,
) -> list[dict[str, object]]:
    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise ValueError("Product not found")

    variants = (
        db.query(ProductVariant).filter(ProductVariant.product_id == product_id).all()
    )
    return [_serialize_variant(variant) for variant in variants]


def get_product_detail_payload(
    db: Session,
    product_id: UUID,
) -> dict[str, object]:
    expired_reservations = release_expired_reservations(db)
    flush_or_rollback(db)

    if expired_reservations["expired_reservations"]:
        commit_or_rollback(db)

    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise ValueError("Product not found")

    variants = (
        db.query(ProductVariant).filter(ProductVariant.product_id == product_id).all()
    )

    variant_payload = []
    for variant in variants:
        payload = _serialize_variant(variant)
        location_totals = _get_location_totals(db, variant.id)
        payload.update(
            {
                "available_stock": sum(location_totals.values()),
                "locations": [
                    {
                        "location_code": code,
                        "available": quantity,
                    }
                    for code, quantity in sorted(location_totals.items())
                ],
            }
        )
        variant_payload.append(payload)

    return {
        "id": str(product.id),
        "name": product.name,
        "slug": product.slug,
        "description": product.description,
        "category": product.category,
        "variants": variant_payload,
    }


def get_variant_stock_payload(
    db: Session,
    product_id: UUID,
    variant_id: UUID,
) -> dict[str, object]:
    expired_reservations = release_expired_reservations(db)
    flush_or_rollback(db)

    if expired_reservations["expired_reservations"]:
        commit_or_rollback(db)

    product = db.query(Product).filter(Product.id == product_id).first()
    if product is None:
        raise ValueError("Product not found")

    variant = (
        db.query(ProductVariant)
        .filter(
            ProductVariant.id == variant_id,
            ProductVariant.product_id == product_id,
        )
        .first()
    )
    if variant is None:
        raise ValueError("Variant not found for this product")

    location_totals = _get_location_totals(db, variant_id)
    return {
        "product_id": str(product.id),
        "variant_id": str(variant.id),
        "material": variant.material,
        "color": variant.color,
        "size_label": variant.size_label,
        "available_stock": sum(location_totals.values()),
        "locations": [
            {
                "location_code": code,
                "available": quantity,
            }
            for code, quantity in sorted(location_totals.items())
        ],
    }
