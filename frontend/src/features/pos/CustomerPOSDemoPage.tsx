import axios from "axios";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  catalogApi,
  type CatalogProductDetail,
  type CatalogProductSummary,
  type CatalogProductVariantDetail,
} from "../../api/catalog";
import {
  posApi,
  type POSCashCheckoutResponse,
  type POSDemoCheckoutRequest,
  type POSReceiptResponse,
} from "../../api/pos";
import {
  displayCategory,
  formatVariantDescription as formatCatalogVariantDescription,
  formatEuroCents,
  formatEuroPrice,
} from "../../catalogDisplay";
import { formatApiErrorDetail } from "../../api/errors";
import styles from "./CustomerPOSDemoPage.module.css";

const RECEIPT_DISPLAY_DURATION_MS = 10_000;

interface DemoCartLine {
  productName: string;
  productCategory: string;
  variant: CatalogProductVariantDetail;
  quantity: number;
}

interface DemoCheckoutAttempt {
  idempotencyKey: string;
  request: POSDemoCheckoutRequest;
}

type DemoReceipt = POSReceiptResponse & { claim_code: string };

function getErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    return formatApiErrorDetail(error.response?.data?.detail, fallback);
  }

  if (error instanceof Error) return error.message;
  return fallback;
}

function isDefinitiveCheckoutRejection(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false;
  return [400, 409, 422].includes(error.response?.status ?? 0);
}

function parseEuroCents(price: string): bigint | null {
  const priceParts = /^(\d+)(?:\.(\d{1,2}))?$/.exec(price);
  if (!priceParts) return null;
  const fraction = (priceParts[2] ?? "").padEnd(2, "0");
  return BigInt(priceParts[1]) * 100n + BigInt(fraction);
}

function formatVariantDescription(
  variant: CatalogProductVariantDetail,
  productCategory: string,
): string {
  return formatCatalogVariantDescription(
    { ...variant, category: productCategory },
    " · ",
  );
}

function formatOrderDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";

  return new Intl.DateTimeFormat("en-IE", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Rome",
  }).format(date);
}

export function CustomerPOSDemoPage() {
  const [products, setProducts] = useState<CatalogProductSummary[]>([]);
  const [isLoadingCatalog, setIsLoadingCatalog] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogAttempt, setCatalogAttempt] = useState(0);
  const [selectedProductId, setSelectedProductId] = useState("");
  const [productDetail, setProductDetail] =
    useState<CatalogProductDetail | null>(null);
  const [productDetailError, setProductDetailError] = useState<string | null>(
    null,
  );
  const [detailAttempt, setDetailAttempt] = useState(0);
  const [cart, setCart] = useState<DemoCartLine[]>([]);
  const [cartError, setCartError] = useState<string | null>(null);
  const [checkoutAttempt, setCheckoutAttempt] =
    useState<DemoCheckoutAttempt | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const [completedSale, setCompletedSale] =
    useState<POSCashCheckoutResponse | null>(null);
  const [receipt, setReceipt] = useState<DemoReceipt | null>(null);
  const [isLoadingReceipt, setIsLoadingReceipt] = useState(false);
  const [receiptError, setReceiptError] = useState<string | null>(null);
  const [isReceiptAcknowledged, setIsReceiptAcknowledged] = useState(false);
  const [isAcknowledgingReceipt, setIsAcknowledgingReceipt] = useState(false);
  const [receiptAcknowledgementError, setReceiptAcknowledgementError] =
    useState<string | null>(null);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const checkoutInProgressRef = useRef(false);
  const acknowledgementInProgressRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();

    async function loadCatalog(): Promise<void> {
      setIsLoadingCatalog(true);
      setCatalogError(null);

      try {
        const catalogProducts = await catalogApi.listProducts(controller.signal);
        if (!controller.signal.aborted) {
          setProducts(catalogProducts);
          setSelectedProductId((currentId) =>
            currentId && catalogProducts.some((product) => product.id === currentId)
              ? currentId
              : (catalogProducts[0]?.id ?? ""),
          );
        }
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          setCatalogError(
            getErrorMessage(error, "The product catalog could not be loaded."),
          );
        }
      } finally {
        if (!controller.signal.aborted) setIsLoadingCatalog(false);
      }
    }

    void loadCatalog();
    return () => controller.abort();
  }, [catalogAttempt]);

  useEffect(() => {
    if (!selectedProductId) return;

    const controller = new AbortController();

    async function loadProductDetail(): Promise<void> {
      try {
        const detail = await catalogApi.getProductDetail(
          selectedProductId,
          controller.signal,
        );
        if (!controller.signal.aborted) setProductDetail(detail);
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          setProductDetailError(
            getErrorMessage(error, "Product variants could not be loaded."),
          );
        }
      }
    }

    void loadProductDetail();
    return () => controller.abort();
  }, [detailAttempt, selectedProductId]);

  const selectedProduct =
    products.find((product) => product.id === selectedProductId) ?? null;
  const isLoadingProductDetail =
    Boolean(selectedProductId) && productDetail === null && productDetailError === null;
  const cartTotalCents = useMemo(() => {
    let totalCents = 0n;
    for (const cartLine of cart) {
      const unitPriceCents = parseEuroCents(cartLine.variant.retail_price);
      if (unitPriceCents === null) return null;
      totalCents += unitPriceCents * BigInt(cartLine.quantity);
    }
    return totalCents;
  }, [cart]);
  const cartIsLocked =
    isSubmitting || checkoutAttempt !== null || completedSale !== null;

  async function loadReceipt(orderId: string): Promise<void> {
    setIsLoadingReceipt(true);
    setReceiptError(null);

    try {
      const fetchedReceipt = await posApi.getDemoReceipt(orderId);
      if (!fetchedReceipt.claim_code) {
        throw new Error("The completed sale receipt has no claim code.");
      }
      setReceipt({
        ...fetchedReceipt,
        claim_code: fetchedReceipt.claim_code,
      });
      setIsReceiptAcknowledged(false);
      setReceiptAcknowledgementError(null);
      setCopyStatus(null);
    } catch (error: unknown) {
      setReceiptError(
        getErrorMessage(error, "The completed sale receipt could not be loaded."),
      );
    } finally {
      setIsLoadingReceipt(false);
    }
  }

  const acknowledgeReceipt = useCallback(
    async (orderId: string): Promise<void> => {
      if (acknowledgementInProgressRef.current) return;

      acknowledgementInProgressRef.current = true;
      setIsAcknowledgingReceipt(true);
      setReceiptAcknowledgementError(null);

      try {
        await posApi.acknowledgeDemoReceipt(orderId);
        setIsReceiptAcknowledged(true);
        setReceipt(null);
        setCopyStatus(null);
      } catch (error: unknown) {
        setReceiptAcknowledgementError(
          getErrorMessage(
            error,
            "The receipt could not be securely closed. Please retry.",
          ),
        );
      } finally {
        acknowledgementInProgressRef.current = false;
        setIsAcknowledgingReceipt(false);
      }
    },
    [],
  );

  useEffect(() => {
    if (!receipt || isReceiptAcknowledged) return;

    const timeoutId = window.setTimeout(() => {
      void acknowledgeReceipt(receipt.order_id);
    }, RECEIPT_DISPLAY_DURATION_MS);

    return () => window.clearTimeout(timeoutId);
  }, [acknowledgeReceipt, isReceiptAcknowledged, receipt]);

  function addVariantToCart(
    productName: string,
    productCategory: string,
    variant: CatalogProductVariantDetail,
  ): void {
    if (cartIsLocked) return;

    const existingLine = cart.find(
      (cartLine) => cartLine.variant.id === variant.id,
    );
    if (
      variant.available_stock < 1 ||
      (existingLine && existingLine.quantity >= variant.available_stock)
    ) {
      setCartError("The selected variant has no additional available stock.");
      return;
    }

    setCart((currentCart) => {
      const matchingLine = currentCart.find(
        (cartLine) => cartLine.variant.id === variant.id,
      );
      if (matchingLine) {
        return currentCart.map((cartLine) =>
          cartLine.variant.id === variant.id
            ? { ...cartLine, quantity: cartLine.quantity + 1 }
            : cartLine,
        );
      }

      return [
        ...currentCart,
        { productName, productCategory, variant, quantity: 1 },
      ];
    });
    setCartError(null);
    setCheckoutError(null);
  }

  function setCartLineQuantity(variantId: string, quantity: number): void {
    if (cartIsLocked) return;

    setCart((currentCart) =>
      currentCart.flatMap((cartLine) => {
        if (cartLine.variant.id !== variantId) return [cartLine];
        if (quantity < 1) return [];
        if (quantity > cartLine.variant.available_stock) return [cartLine];
        return [{ ...cartLine, quantity }];
      }),
    );
    setCartError(null);
    setCheckoutError(null);
  }

  async function handleCheckout(): Promise<void> {
    if (
      checkoutInProgressRef.current ||
      cart.length === 0 ||
      cartTotalCents === null
    ) {
      return;
    }

    checkoutInProgressRef.current = true;
    setIsSubmitting(true);
    setCheckoutError(null);
    const attempt =
      checkoutAttempt ??
      ({
        idempotencyKey: crypto.randomUUID(),
        request: {
          lines: cart.map((cartLine) => ({
            variant_id: cartLine.variant.id,
            quantity: cartLine.quantity,
          })),
        },
      } satisfies DemoCheckoutAttempt);
    setCheckoutAttempt(attempt);

    try {
      const sale = await posApi.createDemoCashCheckout(
        attempt.request,
        attempt.idempotencyKey,
      );
      setCompletedSale(sale);
      setCheckoutAttempt(null);
      setCart([]);
      await loadReceipt(sale.order_id);
    } catch (error: unknown) {
      if (isDefinitiveCheckoutRejection(error)) setCheckoutAttempt(null);
      setCheckoutError(
        getErrorMessage(error, "The demo sale could not be completed."),
      );
    } finally {
      checkoutInProgressRef.current = false;
      setIsSubmitting(false);
    }
  }

  async function handleCopyReceiptCode(): Promise<void> {
    if (!receipt) return;

    try {
      await navigator.clipboard.writeText(receipt.claim_code);
      setCopyStatus("Claim code copied.");
    } catch {
      setCopyStatus("Copy is unavailable. Please write down the claim code.");
    }
  }

  function startAnotherSale(): void {
    if (!isReceiptAcknowledged) return;
    setCompletedSale(null);
    setReceipt(null);
    setIsReceiptAcknowledged(false);
    setReceiptError(null);
    setReceiptAcknowledgementError(null);
    setCheckoutError(null);
    setCartError(null);
    setCheckoutAttempt(null);
  }

  const variantOptions = productDetail?.variants ?? [];

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <Link className={styles.brand} to="/">
            Half Moon
          </Link>
          <p>Customer POS demo</p>
        </div>
        <nav className={styles.headerLinks} aria-label="Demo navigation">
          <Link to="/">Storefront</Link>
          <Link to="/account/claim-pos-order">Claim a purchase</Link>
        </nav>
      </header>

      <section className={styles.intro}>
        <p className={styles.eyebrow}>Customer demo</p>
        <h1>Try a store purchase</h1>
        <p>
          This is a cash-only simulation. No real payment is taken. Keep the
          one-time receipt code to claim the purchase in a verified customer
          account.
        </p>
      </section>

      {!completedSale && (
        <div className={styles.terminal}>
          <section className={styles.panel} aria-labelledby="catalog-heading">
            <h2 id="catalog-heading">Choose a product</h2>
            {isLoadingCatalog && <p role="status">Loading products...</p>}
            {catalogError && (
              <div className={styles.error} role="alert">
                <p>{catalogError}</p>
                <button
                  type="button"
                  className={styles.textButton}
                  onClick={() => setCatalogAttempt((attempt) => attempt + 1)}
                >
                  Try again
                </button>
              </div>
            )}
            {!isLoadingCatalog && !catalogError && products.length === 0 && (
              <p>No products are available for the demo.</p>
            )}
            {!isLoadingCatalog && !catalogError && products.length > 0 && (
              <>
                <label className={styles.field}>
                  <span>Product</span>
                  <select
                    value={selectedProductId}
                    disabled={cartIsLocked}
                    onChange={(event) => {
                      setSelectedProductId(event.target.value);
                      setProductDetail(null);
                      setProductDetailError(null);
                    }}
                  >
                    {products.map((product) => (
                      <option key={product.id} value={product.id}>
                        {product.name} · {displayCategory(product.category)}
                      </option>
                    ))}
                  </select>
                </label>
                {isLoadingProductDetail && (
                  <p role="status">Loading product variants...</p>
                )}
                {productDetailError && (
                  <div className={styles.error} role="alert">
                    <p>{productDetailError}</p>
                    <button
                      type="button"
                      className={styles.textButton}
                      onClick={() => {
                        setProductDetailError(null);
                        setDetailAttempt((attempt) => attempt + 1);
                      }}
                    >
                      Try again
                    </button>
                  </div>
                )}
                {!isLoadingProductDetail &&
                  !productDetailError &&
                  productDetail && (
                    <div className={styles.variantList}>
                      {variantOptions.map((variant) => (
                        <article className={styles.variant} key={variant.id}>
                          <div>
                            <h3>
                              {formatVariantDescription(
                                variant,
                                productDetail.category,
                              )}
                            </h3>
                            <p>{formatEuroPrice(variant.retail_price)}</p>
                            <p>
                              {variant.available_stock > 0
                                ? `${variant.available_stock} available`
                                : "Out of stock"}
                            </p>
                          </div>
                          <button
                            className={styles.button}
                            type="button"
                            onClick={() =>
                              addVariantToCart(
                                selectedProduct?.name ?? productDetail.name,
                                productDetail.category,
                                variant,
                              )
                            }
                            disabled={
                              cartIsLocked || variant.available_stock < 1
                            }
                          >
                            {variant.available_stock > 0
                              ? "Add"
                              : "Out of stock"}
                          </button>
                        </article>
                      ))}
                    </div>
                  )}
              </>
            )}
          </section>

          <section className={styles.panel} aria-labelledby="basket-heading">
            <h2 id="basket-heading">Basket</h2>
            {cart.length === 0 ? (
              <p>Your basket is empty.</p>
            ) : (
              <ul className={styles.cartList}>
                {cart.map((cartLine) => {
                  const unitPriceCents = parseEuroCents(
                    cartLine.variant.retail_price,
                  );
                  const lineTotal =
                    unitPriceCents === null
                      ? "Price unavailable"
                      : formatEuroCents(
                          unitPriceCents * BigInt(cartLine.quantity),
                        );

                  return (
                    <li className={styles.cartLine} key={cartLine.variant.id}>
                      <div>
                        <h3>{cartLine.productName}</h3>
                        <p>
                          {formatVariantDescription(
                            cartLine.variant,
                            cartLine.productCategory,
                          )}
                        </p>
                        <p>{formatEuroPrice(cartLine.variant.retail_price)} each</p>
                      </div>
                      <div className={styles.quantity}>
                        <button
                          className={styles.quantityButton}
                          type="button"
                          disabled={cartIsLocked}
                          aria-label={`Remove one ${cartLine.productName}`}
                          onClick={() =>
                            setCartLineQuantity(
                              cartLine.variant.id,
                              cartLine.quantity - 1,
                            )
                          }
                        >
                          −
                        </button>
                        <span aria-live="polite">{cartLine.quantity}</span>
                        <button
                          className={styles.quantityButton}
                          type="button"
                          disabled={
                            cartIsLocked ||
                            cartLine.quantity >= cartLine.variant.available_stock
                          }
                          aria-label={`Add one ${cartLine.productName}`}
                          onClick={() =>
                            setCartLineQuantity(
                              cartLine.variant.id,
                              cartLine.quantity + 1,
                            )
                          }
                        >
                          +
                        </button>
                      </div>
                      <strong>{lineTotal}</strong>
                    </li>
                  );
                })}
              </ul>
            )}

            {cartError && (
              <p className={styles.error} role="alert">
                {cartError}
              </p>
            )}
            {checkoutError && (
              <p className={styles.error} role="alert">
                {checkoutError}
              </p>
            )}
            {checkoutAttempt && (
              <p className={styles.muted}>
                Retry uses the same basket and idempotency key.
              </p>
            )}
            <div className={styles.total}>
              <span>Total</span>
              <strong>
                {cartTotalCents === null
                  ? "Price unavailable"
                  : formatEuroCents(cartTotalCents)}
              </strong>
            </div>
            <button
              className={styles.button}
              type="button"
              onClick={() => void handleCheckout()}
              disabled={
                cart.length === 0 ||
                cartTotalCents === null ||
                isSubmitting ||
                completedSale !== null
              }
            >
              {isSubmitting
                ? "Completing demo sale..."
                : checkoutAttempt
                  ? "Retry demo sale"
                  : "Complete cash demo sale"}
            </button>
          </section>
        </div>
      )}

      {completedSale && (
        <section className={styles.receipt} aria-labelledby="receipt-heading">
          <p className={styles.eyebrow}>Sale complete</p>
          <h2 id="receipt-heading">Order {completedSale.order_number}</h2>
          <p>
            Simulated cash sale · {formatEuroPrice(completedSale.total_gross)}
          </p>
          {isLoadingReceipt && <p role="status">Preparing your receipt...</p>}
          {receiptError && (
            <div className={styles.error} role="alert">
              <p>{receiptError}</p>
              <button
                className={styles.button}
                type="button"
                onClick={() => void loadReceipt(completedSale.order_id)}
                disabled={isLoadingReceipt}
              >
                Retry receipt
              </button>
            </div>
          )}
          {receipt && !isReceiptAcknowledged && (
            <>
              <p className={styles.claimCodeLabel}>One-time claim code</p>
              <p className={styles.claimCode}>{receipt.claim_code}</p>
              <p>
                Receipt code available until{" "}
                {receipt.claim_expires_at
                  ? formatOrderDate(receipt.claim_expires_at)
                  : "the 14-day claim deadline"}
                . This display closes automatically after ten seconds.
              </p>
              <button
                className={styles.button}
                type="button"
                onClick={() => void handleCopyReceiptCode()}
              >
                Copy claim code
              </button>
              {copyStatus && <p role="status">{copyStatus}</p>}
              {isAcknowledgingReceipt && (
                <p role="status">Closing the one-time receipt...</p>
              )}
              {receiptAcknowledgementError && (
                <div className={styles.error} role="alert">
                  <p>{receiptAcknowledgementError}</p>
                  <button
                    className={styles.button}
                    type="button"
                    onClick={() =>
                      void acknowledgeReceipt(completedSale.order_id)
                    }
                    disabled={isAcknowledgingReceipt}
                  >
                    Retry receipt closure
                  </button>
                </div>
              )}
              <Link
                to={`/account/claim-pos-order?order_number=${encodeURIComponent(completedSale.order_number)}`}
              >
                Claim this purchase
              </Link>
            </>
          )}
          {isReceiptAcknowledged && (
            <div className={styles.success} role="status">
              <p>The one-time receipt display has closed.</p>
              <p>
                Claim the purchase using the order number and the code you
                copied from the receipt.
              </p>
              <Link
                to={`/account/claim-pos-order?order_number=${encodeURIComponent(completedSale.order_number)}`}
              >
                Claim this purchase
              </Link>
              <button
                className={styles.button}
                type="button"
                onClick={startAnotherSale}
              >
                Start another demo sale
              </button>
            </div>
          )}
        </section>
      )}
    </main>
  );
}
