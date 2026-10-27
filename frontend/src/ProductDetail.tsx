import axios from "axios";
import { useEffect, useMemo, useRef, useState } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import {
  catalogApi,
  type CatalogProductDetail,
} from "./api/catalog";
import { formatApiErrorDetail } from "./api/errors";
import { useCart } from "./cart/CartContext";
import { createCollectionReturnHref } from "./collectionFilters";
import {
  displayCategory,
  displayColor,
  displaySize,
  formatEuroPrice,
} from "./catalogDisplay";
import {
  CartContent,
  StorefrontFooter,
  StorefrontHeader,
} from "./Storefront";
import styles from "./ProductDetail.module.css";
import storeStyles from "./Storefront.module.css";

type ProductDetailData = CatalogProductDetail;

function getErrorMessage(error: unknown): string {
  const fallback = "The product could not be loaded.";

  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    return formatApiErrorDetail(error.response?.data?.detail, fallback);
  }

  if (error instanceof Error) {
    return error.message;
  }

  return fallback;
}

function ProductDetailPage() {
  const { slug } = useParams();
  const [searchParams, setSearchParams] = useSearchParams();
  const { addItem, items: cartItems } = useCart();
  const [product, setProduct] = useState<ProductDetailData | null>(null);
  const [selectedVariantId, setSelectedVariantId] = useState<string | null>(
    null,
  );
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [isBagOpen, setIsBagOpen] = useState(false);
  const [showAddedMessage, setShowAddedMessage] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const bagDialogRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const controller = new AbortController();

    async function loadProduct(): Promise<void> {
      setIsLoading(true);
      setLoadError(null);

      try {
        const catalogueProducts = await catalogApi.listProducts(
          controller.signal,
        );
        const catalogueProduct = catalogueProducts.find(
          (item) => item.slug === slug,
        );

        if (!catalogueProduct) {
          throw new Error("Product not found.");
        }

        const detailResponse = await catalogApi.getProductDetail(
          catalogueProduct.id,
          controller.signal,
        );

        if (controller.signal.aborted) {
          return;
        }

        setProduct({
          ...detailResponse,
          image_url: catalogueProduct.image_url,
        });
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          setProduct(null);
          setLoadError(getErrorMessage(error));
        }
      } finally {
        if (!controller.signal.aborted) {
          setIsLoading(false);
        }
      }
    }

    void loadProduct();

    return () => controller.abort();
  }, [slug, loadAttempt]);

  useEffect(() => {
    const dialog = bagDialogRef.current;

    if (!dialog) {
      return;
    }

    if (isBagOpen && !dialog.open) {
      dialog.showModal();
    } else if (!isBagOpen && dialog.open) {
      dialog.close();
    }
  }, [isBagOpen]);

  useEffect(() => {
    if (!showAddedMessage) {
      return;
    }

    const timeoutId = window.setTimeout(() => {
      setShowAddedMessage(false);
    }, 2200);

    return () => window.clearTimeout(timeoutId);
  }, [showAddedMessage]);

  const colors = useMemo(
    () =>
      product
        ? [...new Set(product.variants.map((variant) => variant.color))]
        : [],
    [product],
  );

  const requestedColor = searchParams.get("color");
  const selectedColor =
    colors.find((color) => color === requestedColor) ??
    colors.find((color) => color === "Yellow Gold") ??
    colors[0] ??
    null;
  const backToCollectionHref = createCollectionReturnHref(searchParams);

  useEffect(() => {
    if (!product || colors.length === 0) {
      return;
    }

    if (selectedColor && requestedColor !== selectedColor) {
      setSearchParams(
        (currentSearchParams) => {
          const nextSearchParams = new URLSearchParams(currentSearchParams);
          nextSearchParams.set("color", selectedColor);
          return nextSearchParams;
        },
        { replace: true },
      );
    }
  }, [colors, product, requestedColor, selectedColor, setSearchParams]);

  const variantsForColor = useMemo(
    () =>
      product && selectedColor
        ? product.variants
            .filter((variant) => variant.color === selectedColor)
            .sort((left, right) =>
              left.size_label.localeCompare(right.size_label, undefined, {
                numeric: true,
              }),
            )
        : [],
    [product, selectedColor],
  );

  const selectedVariant =
    variantsForColor.length === 1
      ? variantsForColor[0]
      : variantsForColor.find(
          (variant) => variant.id === selectedVariantId,
        ) ?? null;
  const displayedVariant = selectedVariant ?? variantsForColor[0] ?? null;
  const imageUrl = displayedVariant?.image_url ?? product?.image_url ?? "";
  const displayedPrice = displayedVariant
    ? formatEuroPrice(displayedVariant.retail_price)
    : "Price unavailable";
  const hasAvailableSize = variantsForColor.some(
    (variant) => variant.available_stock > 0,
  );
  const availability = selectedVariant
    ? selectedVariant.available_stock > 0
      ? "Available"
      : "Out of stock"
    : hasAvailableSize
      ? "Available"
      : "Out of stock";
  const cartQuantity =
    cartItems.find((item) => item.variantId === selectedVariant?.id)
      ?.quantity ?? 0;
  const isMaximumQuantityInBag =
    selectedVariant !== null &&
    cartQuantity >= selectedVariant.available_stock;

  function selectColor(color: string): void {
    setSelectedVariantId(null);
    setAddError(null);
    setSearchParams((currentSearchParams) => {
      const nextSearchParams = new URLSearchParams(currentSearchParams);
      nextSearchParams.set("color", color);
      return nextSearchParams;
    });
  }

  function handleAddToBag(): void {
    if (
      !product ||
      !selectedVariant ||
      selectedVariant.available_stock < 1 ||
      displayedPrice === "Price unavailable"
    ) {
      return;
    }

    const wasAdded = addItem({
      productId: product.id,
      productSlug: product.slug,
      productName: product.name,
      productCategory: product.category,
      variantId: selectedVariant.id,
      color: selectedVariant.color,
      material: selectedVariant.material,
      sizeSystem: selectedVariant.size_system,
      sizeLabel: selectedVariant.size_label,
      retailPrice: selectedVariant.retail_price,
      imageUrl: selectedVariant.image_url ?? product.image_url,
      availableStock: selectedVariant.available_stock,
    });

    if (wasAdded) {
      setShowAddedMessage(true);
      setAddError(null);
      window.scrollTo({ top: 0, behavior: "smooth" });
      return;
    }

    setAddError("The available quantity is already in your bag.");
  }

  return (
    <div className={storeStyles.siteShell}>
      <StorefrontHeader
        mode="product"
        onOpenCart={() => setIsBagOpen(true)}
        backToCollectionHref={backToCollectionHref}
      />

      <main className={styles.productPage}>
        {isLoading && (
          <p className={styles.statusMessage} role="status">
            Loading product details…
          </p>
        )}

        {!isLoading && loadError && (
          <div className={styles.errorMessage} role="alert">
            <p>The product could not be loaded: {loadError}</p>
            <button
              className={storeStyles.textButton}
              type="button"
              onClick={() => setLoadAttempt((attempt) => attempt + 1)}
            >
              Retry
            </button>
          </div>
        )}

        {!isLoading && !loadError && product && (
          <div className={styles.productLayout}>
            <div className={styles.gallery}>
              <img
                className={styles.productImage}
                src={imageUrl}
                alt={`${product.name} in ${displayColor(selectedColor ?? "")}`}
              />
            </div>

            <section
              className={styles.productInformation}
              aria-labelledby="product-title"
            >
              <p className={styles.category}>
                {displayCategory(product.category)}
              </p>
              <h1 id="product-title">{product.name}</h1>
              <p className={styles.description}>{product.description}</p>

              {displayedVariant && (
                <p className={styles.material}>
                  Material: {displayedVariant.material}
                </p>
              )}

              <p className={styles.price}>{displayedPrice}</p>

              <p className={styles.optionLabel}>Colour</p>
              <div className={styles.optionList} aria-label="Choose a colour">
                {colors.map((color) => (
                  <button
                    className={
                      color === selectedColor
                        ? `${styles.optionButton} ${styles.optionButtonSelected}`
                        : styles.optionButton
                    }
                    key={color}
                    type="button"
                    aria-pressed={color === selectedColor}
                    onClick={() => selectColor(color)}
                  >
                    {displayColor(color)}
                  </button>
                ))}
              </div>

              {variantsForColor.length > 1 && (
                <>
                  <p className={styles.optionLabel}>
                    {product.category === "RING" ? "Size" : "Option"}
                  </p>
                  <div
                    className={styles.optionList}
                    aria-label={
                      product.category === "RING"
                        ? "Choose a size"
                        : "Choose a variant"
                    }
                  >
                    {variantsForColor.map((variant, variantIndex) => (
                      <button
                        className={
                          variant.id === selectedVariant?.id
                            ? `${styles.optionButton} ${styles.optionButtonSelected}`
                            : styles.optionButton
                        }
                        key={variant.id}
                        type="button"
                        aria-pressed={variant.id === selectedVariant?.id}
                        disabled={variant.available_stock < 1}
                        onClick={() => {
                          setSelectedVariantId(variant.id);
                          setAddError(null);
                        }}
                      >
                        {product.category === "RING"
                          ? displaySize(
                              variant.size_system,
                              variant.size_label,
                            )
                          : `Option ${variantIndex + 1}`}
                      </button>
                    ))}
                  </div>
                </>
              )}

              <p className={styles.availability} role="status">
                {availability}
              </p>

              <button
                className={styles.addButton}
                type="button"
                disabled={
                  !selectedVariant ||
                  selectedVariant.available_stock < 1 ||
                  displayedPrice === "Price unavailable" ||
                  isMaximumQuantityInBag
                }
                onClick={handleAddToBag}
              >
                Add to Cart
              </button>

              {showAddedMessage && (
                <p className={styles.addedMessage} role="status">
                  Added to your bag
                </p>
              )}
              {addError && (
                <p className={styles.addedMessage} role="alert">
                  {addError}
                </p>
              )}
            </section>
          </div>
        )}
      </main>

      <StorefrontFooter />

      <dialog
        ref={bagDialogRef}
        className={`${storeStyles.drawer} ${storeStyles.drawerRight}`}
        aria-labelledby="cart-title"
        onCancel={(event) => {
          event.preventDefault();
          setIsBagOpen(false);
        }}
        onClick={(event) => {
          if (event.target === event.currentTarget) {
            setIsBagOpen(false);
          }
        }}
      >
        <CartContent onClose={() => setIsBagOpen(false)} />
      </dialog>
    </div>
  );
}

export default ProductDetailPage;
