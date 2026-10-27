import axios from "axios";
import {
  CardElement,
  Elements,
  useElements,
  useStripe,
} from "@stripe/react-stripe-js";
import { loadStripe } from "@stripe/stripe-js";
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { Link } from "react-router-dom";
import {
  catalogApi,
  type CatalogProductDetail,
  type CatalogProductSummary,
  type CatalogProductVariantDetail,
} from "../../api/catalog";
import {
  posApi,
  type POSCardCheckoutRequest,
  type POSCardCheckoutResponse,
  type POSCashCheckoutRequest,
  type POSCashCheckoutResponse,
  type POSOrderStatusResponse,
  type POSReceiptResponse,
} from "../../api/pos";
import {
  displayCategory,
  formatVariantDescription,
  formatEuroCents,
  formatEuroPrice,
} from "../../catalogDisplay";
import { formatApiErrorDetail } from "../../api/errors";
import { useAuth } from "../auth/AuthProvider";
import styles from "./PosPage.module.css";

const RECEIPT_DISPLAY_DURATION_MS = 10_000;
const CARD_STATUS_POLL_INTERVAL_MS = 1_000;
const CARD_IDLE_POLL_INTERVAL_MS = 5_000;
const CARD_STATUS_MAX_POLL_INTERVAL_MS = 30_000;
const stripePublishableKey =
  import.meta.env.VITE_STRIPE_PUBLISHABLE_KEY?.trim() ?? "";
const stripePromise = stripePublishableKey
  ? loadStripe(stripePublishableKey)
  : null;

interface POSCartLine {
  productName: string;
  productCategory: string;
  variant: CatalogProductVariantDetail;
  quantity: number;
}

interface POSCheckoutAttempt {
  idempotencyKey: string;
  request: POSCashCheckoutRequest;
}

interface POSCardCheckoutAttempt {
  idempotencyKey: string;
  request: POSCardCheckoutRequest;
}

interface POSCardPaymentFormProps {
  clientSecret: string;
  isAwaitingWebhook: boolean;
  totalGross: string;
  onPaymentConfirmationStarted: () => void;
}

type POSCompletedSale = POSCashCheckoutResponse | POSOrderStatusResponse;

type POSReceiptWithClaimCode = POSReceiptResponse & { claim_code: string };

function getErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    return formatApiErrorDetail(error.response?.data?.detail, fallback);
  }

  if (error instanceof Error) return error.message;
  return fallback;
}

function POSCardPaymentForm({
  clientSecret,
  isAwaitingWebhook,
  totalGross,
  onPaymentConfirmationStarted,
}: POSCardPaymentFormProps) {
  const stripe = useStripe();
  const elements = useElements();
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [isCardComplete, setIsCardComplete] = useState(false);
  const [cardError, setCardError] = useState<string | null>(null);

  async function handleSubmit(
    event: React.FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    setCardError(null);

    if (!stripe || !elements) {
      setCardError("The secure card form is still loading. Please retry.");
      return;
    }

    const cardElement = elements.getElement(CardElement);
    if (!cardElement) {
      setCardError("The secure card form is unavailable. Please reload POS.");
      return;
    }

    setIsSubmitting(true);

    try {
      const { error, paymentIntent } = await stripe.confirmCardPayment(
        clientSecret,
        { payment_method: { card: cardElement } },
      );

      if (error) {
        setCardError(
          error.message ?? "The card payment was not accepted. Please retry.",
        );
        return;
      }

      if (!paymentIntent) {
        setCardError(
          "Stripe did not confirm the card attempt. Please retry the payment.",
        );
        return;
      }

      if (paymentIntent.status === "requires_payment_method") {
        setCardError(
          "The card payment was declined. Use another card or retry before the reservation expires.",
        );
        return;
      }

      onPaymentConfirmationStarted();
    } catch (error: unknown) {
      setCardError(
        getErrorMessage(error, "The card payment could not be confirmed."),
      );
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <form className={styles.cardForm} onSubmit={(event) => void handleSubmit(event)}>
      <label className={styles.cardField}>
        <span>Card details</span>
        <div className={styles.cardElement}>
          <CardElement
            options={{ hidePostalCode: true }}
            onChange={(event) => {
              setIsCardComplete(event.complete);
              setCardError(event.error?.message ?? null);
            }}
          />
        </div>
      </label>
      {cardError && (
        <p className={styles.error} role="alert">
          {cardError}
        </p>
      )}
      <p className={styles.mutedText}>
        Card details are sent directly to Stripe and are not stored by Half
        Moon.
      </p>
      <button
        className={styles.checkoutButton}
        type="submit"
        disabled={
          !stripe ||
          !elements ||
          !isCardComplete ||
          isSubmitting ||
          isAwaitingWebhook
        }
      >
        {isSubmitting
          ? "Confirming card..."
          : isAwaitingWebhook
            ? "Waiting for payment confirmation..."
            : `Pay ${formatEuroPrice(totalGross)}`}
      </button>
    </form>
  );
}

function parseEuroCents(price: string): bigint | null {
  const priceParts = /^(\d+)(?:\.(\d{1,2}))?$/.exec(price);
  if (!priceParts) return null;

  const fraction = (priceParts[2] ?? "").padEnd(2, "0");
  return BigInt(priceParts[1]) * 100n + BigInt(fraction);
}

function formatCartLineTotal(cartLine: POSCartLine): string {
  const unitPriceCents = parseEuroCents(cartLine.variant.retail_price);
  if (unitPriceCents === null) return "Price unavailable";

  return formatEuroCents(unitPriceCents * BigInt(cartLine.quantity));
}

function getVariantDescription(
  variant: CatalogProductVariantDetail,
  productCategory: string,
): string {
  return formatVariantDescription(
    { ...variant, category: productCategory },
    " · ",
  );
}

function getLocationLabel(locationCode: string): string {
  if (locationCode === "PHYSICAL_STORE") return "Store";
  if (locationCode === "MAIN_WAREHOUSE") return "Warehouse";
  return locationCode;
}

function getLocationSummary(
  variant: CatalogProductVariantDetail,
): string {
  if (variant.locations.length === 0) return "No available stock";

  return variant.locations
    .map(
      (location) =>
        `${getLocationLabel(location.location_code)}: ${location.available}`,
    )
    .join(" · ");
}

function isDefinitiveCheckoutRejection(error: unknown): boolean {
  if (!axios.isAxiosError(error)) return false;

  return [400, 409, 422].includes(error.response?.status ?? 0);
}

export function PosPage() {
  const { session, logout } = useAuth();
  const [products, setProducts] = useState<CatalogProductSummary[]>([]);
  const [isLoadingCatalog, setIsLoadingCatalog] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [catalogLoadAttempt, setCatalogLoadAttempt] = useState(0);
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedProductId, setSelectedProductId] = useState<string | null>(
    null,
  );
  const [productDetail, setProductDetail] =
    useState<CatalogProductDetail | null>(null);
  const [isLoadingProductDetail, setIsLoadingProductDetail] = useState(false);
  const [productDetailError, setProductDetailError] = useState<string | null>(
    null,
  );
  const [productDetailLoadAttempt, setProductDetailLoadAttempt] = useState(0);
  const [cart, setCart] = useState<POSCartLine[]>([]);
  const [cartError, setCartError] = useState<string | null>(null);
  const [checkoutAttempt, setCheckoutAttempt] =
    useState<POSCheckoutAttempt | null>(null);
  const [cardCheckoutAttempt, setCardCheckoutAttempt] =
    useState<POSCardCheckoutAttempt | null>(null);
  const [cardCheckout, setCardCheckout] =
    useState<POSCardCheckoutResponse | null>(null);
  const [isAwaitingCardWebhook, setIsAwaitingCardWebhook] = useState(false);
  const [hasExpiredCardReservation, setHasExpiredCardReservation] =
    useState(false);
  const [cardStatusError, setCardStatusError] = useState<string | null>(null);
  const [selectedPaymentMethod, setSelectedPaymentMethod] = useState<
    "CASH" | "CARD"
  >("CASH");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [checkoutError, setCheckoutError] = useState<string | null>(null);
  const [completedSale, setCompletedSale] =
    useState<POSCompletedSale | null>(null);
  const [receipt, setReceipt] = useState<POSReceiptWithClaimCode | null>(null);
  const [isLoadingReceipt, setIsLoadingReceipt] = useState(false);
  const [receiptError, setReceiptError] = useState<string | null>(null);
  const [receiptAcknowledgementError, setReceiptAcknowledgementError] =
    useState<string | null>(null);
  const [isAcknowledgingReceipt, setIsAcknowledgingReceipt] = useState(false);
  const [copyStatus, setCopyStatus] = useState<string | null>(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const checkoutInProgressRef = useRef(false);
  const receiptAcknowledgementInProgressRef = useRef(false);

  useEffect(() => {
    const controller = new AbortController();

    async function loadCatalog(): Promise<void> {
      setIsLoadingCatalog(true);
      setCatalogError(null);

      try {
        const catalogProducts = await catalogApi.listProducts(
          controller.signal,
        );
        if (!controller.signal.aborted) setProducts(catalogProducts);
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
  }, [catalogLoadAttempt]);

  useEffect(() => {
    if (!selectedProductId) return;

    const productId = selectedProductId;
    const controller = new AbortController();

    async function loadProductDetail(): Promise<void> {
      setIsLoadingProductDetail(true);
      setProductDetailError(null);
      setProductDetail(null);

      try {
        const detail = await catalogApi.getProductDetail(
          productId,
          controller.signal,
        );
        if (!controller.signal.aborted) setProductDetail(detail);
      } catch (error: unknown) {
        if (!controller.signal.aborted) {
          setProductDetailError(
            getErrorMessage(error, "Product variants could not be loaded."),
          );
        }
      } finally {
        if (!controller.signal.aborted) setIsLoadingProductDetail(false);
      }
    }

    void loadProductDetail();

    return () => controller.abort();
  }, [productDetailLoadAttempt, selectedProductId]);

  const selectedProduct =
    products.find((product) => product.id === selectedProductId) ?? null;
  const filteredProducts = useMemo(() => {
    const normalizedSearch = searchTerm.trim().toLocaleLowerCase();
    if (!normalizedSearch) return products;

    return products.filter((product) =>
      `${product.name} ${product.category} ${product.slug}`
        .toLocaleLowerCase()
        .includes(normalizedSearch),
    );
  }, [products, searchTerm]);
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
    isSubmitting ||
    checkoutAttempt !== null ||
    cardCheckoutAttempt !== null ||
    cardCheckout !== null;

  async function handleLogout(): Promise<void> {
    setIsLoggingOut(true);
    setLogoutError(null);

    try {
      await logout();
    } catch (error: unknown) {
      setLogoutError(getErrorMessage(error, "Sign out failed. Please retry."));
    } finally {
      setIsLoggingOut(false);
    }
  }

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

  const loadReceipt = useCallback(async (orderId: string): Promise<void> => {
    setIsLoadingReceipt(true);
    setReceiptError(null);

    try {
      const fetchedReceipt = await posApi.getReceipt(orderId);
      if (!fetchedReceipt.claim_code) {
        throw new Error(
          "The sale completed, but its receipt claim code is unavailable.",
        );
      }
      setReceipt({
        ...fetchedReceipt,
        claim_code: fetchedReceipt.claim_code,
      });
      setReceiptAcknowledgementError(null);
      setCopyStatus(null);
    } catch (error: unknown) {
      setReceiptError(
        getErrorMessage(error, "The completed sale receipt could not be loaded."),
      );
    } finally {
      setIsLoadingReceipt(false);
    }
  }, []);

  useEffect(() => {
    if (!cardCheckout) return;

    const activeCardCheckout = cardCheckout;
    const controller = new AbortController();
    let timeoutId: number | undefined;
    let pollInterval = isAwaitingCardWebhook
      ? CARD_STATUS_POLL_INTERVAL_MS
      : CARD_IDLE_POLL_INTERVAL_MS;

    async function pollCardOrderStatus(): Promise<void> {
      try {
        const status = await posApi.getOrderStatus(
          activeCardCheckout.order_id,
          controller.signal,
        );
        if (controller.signal.aborted) return;
        if (
          status.order_id !== activeCardCheckout.order_id ||
          status.payment_method !== "CARD"
        ) {
          throw new Error("The backend returned a different POS order.");
        }

        if (
          status.order_status === "COMPLETED" &&
          (status.payment_status === "PAID" ||
            status.payment_status === "PARTIALLY_REFUNDED" ||
            status.payment_status === "REFUNDED")
        ) {
          setCardStatusError(null);
          setCardCheckout(null);
          setCardCheckoutAttempt(null);
          setIsAwaitingCardWebhook(false);
          setHasExpiredCardReservation(false);
          setCheckoutError(null);
          setCompletedSale(status);
          await loadReceipt(status.order_id);
          return;
        }

        if (status.order_status === "EXPIRED") {
          if (status.payment_status === "REFUNDED") {
            setCardCheckout(null);
            setCardCheckoutAttempt(null);
            setIsAwaitingCardWebhook(false);
            setHasExpiredCardReservation(false);
            setCardStatusError(null);
            setCheckoutError(
              "The card payment was refunded after the reservation expired. No sale was completed. Review the basket before starting another sale.",
            );
            return;
          }

          if (isAwaitingCardWebhook) {
            setHasExpiredCardReservation(true);
            setCardStatusError(null);
            pollInterval = Math.min(
              Math.max(pollInterval, CARD_IDLE_POLL_INTERVAL_MS) * 2,
              CARD_STATUS_MAX_POLL_INTERVAL_MS,
            );
          } else {
            setCardCheckout(null);
            setCardCheckoutAttempt(null);
            setIsAwaitingCardWebhook(false);
            setHasExpiredCardReservation(false);
            setCardStatusError(null);
            setCheckoutError(
              "The card reservation expired before payment confirmation. Review the basket and start a new sale.",
            );
            return;
          }
        } else if (status.order_status === "CANCELLED") {
          setCardCheckout(null);
          setCardCheckoutAttempt(null);
          setIsAwaitingCardWebhook(false);
          setHasExpiredCardReservation(false);
          setCardStatusError(null);
          setCheckoutError(
            "The pending card order was canceled by the payment provider. Review the basket and start a new sale.",
          );
          return;
        } else if (
          status.order_status !== "PENDING_PAYMENT" ||
          status.payment_status !== "UNPAID"
        ) {
          throw new Error(
            "The backend returned an unexpected card payment state.",
          );
        } else {
          setHasExpiredCardReservation(false);
        }

        setCardStatusError(null);
      } catch (error: unknown) {
        if (controller.signal.aborted) return;
        setCardStatusError(
          getErrorMessage(
            error,
            "Card payment status could not be checked. Retrying automatically.",
          ),
        );
      }

      if (!controller.signal.aborted) {
        timeoutId = window.setTimeout(
          () => void pollCardOrderStatus(),
          pollInterval,
        );
      }
    }

    void pollCardOrderStatus();

    return () => {
      controller.abort();
      if (timeoutId !== undefined) window.clearTimeout(timeoutId);
    };
  }, [cardCheckout, isAwaitingCardWebhook, loadReceipt]);

  async function handleCashCheckout(): Promise<void> {
    if (
      selectedPaymentMethod !== "CASH" ||
      cart.length === 0 ||
      isSubmitting ||
      completedSale !== null ||
      checkoutInProgressRef.current
    ) {
      return;
    }

    checkoutInProgressRef.current = true;
    setIsSubmitting(true);
    setCheckoutError(null);

    try {
      const attempt =
        checkoutAttempt ??
        ({
          idempotencyKey: window.crypto.randomUUID(),
          request: {
            lines: cart.map((cartLine) => ({
              variant_id: cartLine.variant.id,
              quantity: cartLine.quantity,
            })),
            payment_method: "CASH",
          },
        } satisfies POSCheckoutAttempt);
      setCheckoutAttempt(attempt);

      const sale = await posApi.createCashCheckout(
        attempt.request,
        attempt.idempotencyKey,
      );
      if (
        sale.payment_method !== "CASH" ||
        sale.order_status !== "COMPLETED" ||
        sale.payment_status !== "PAID"
      ) {
        throw new Error(
          "The backend did not confirm the cash sale as completed.",
        );
      }

      setCheckoutAttempt(null);
      setCompletedSale(sale);
      await loadReceipt(sale.order_id);
    } catch (error: unknown) {
      setCheckoutError(
        getErrorMessage(error, "The cash sale could not be completed."),
      );
      if (isDefinitiveCheckoutRejection(error)) {
        setCheckoutAttempt(null);
      }
    } finally {
      checkoutInProgressRef.current = false;
      setIsSubmitting(false);
    }
  }

  async function handleCardCheckout(): Promise<void> {
    if (!stripePublishableKey) {
      setCheckoutError(
        "Card checkout is not configured. Add VITE_STRIPE_PUBLISHABLE_KEY to the frontend environment before starting a card sale.",
      );
      return;
    }

    if (
      selectedPaymentMethod !== "CARD" ||
      cart.length === 0 ||
      isSubmitting ||
      completedSale !== null ||
      cardCheckout !== null ||
      checkoutInProgressRef.current
    ) {
      return;
    }

    checkoutInProgressRef.current = true;
    setIsSubmitting(true);
    setCheckoutError(null);
    setCardStatusError(null);

    try {
      const attempt =
        cardCheckoutAttempt ??
        ({
          idempotencyKey: window.crypto.randomUUID(),
          request: {
            lines: cart.map((cartLine) => ({
              variant_id: cartLine.variant.id,
              quantity: cartLine.quantity,
            })),
            payment_method: "CARD",
          },
        } satisfies POSCardCheckoutAttempt);
      setCardCheckoutAttempt(attempt);

      const checkout = await posApi.createCardCheckout(
        attempt.request,
        attempt.idempotencyKey,
      );
      if (
        checkout.payment_method !== "CARD" ||
        checkout.order_status !== "PENDING_PAYMENT" ||
        checkout.payment_status !== "UNPAID" ||
        !checkout.payment_intent_client_secret
      ) {
        throw new Error(
          "The backend did not return a pending card payment for this sale.",
        );
      }

      setCardCheckout(checkout);
    } catch (error: unknown) {
      setCheckoutError(
        getErrorMessage(error, "Card checkout could not be started."),
      );
      if (isDefinitiveCheckoutRejection(error)) {
        setCardCheckoutAttempt(null);
      }
    } finally {
      checkoutInProgressRef.current = false;
      setIsSubmitting(false);
    }
  }

  const finishReceipt = useCallback(async (): Promise<void> => {
    if (!receipt || receiptAcknowledgementInProgressRef.current) return;

    receiptAcknowledgementInProgressRef.current = true;
    setIsAcknowledgingReceipt(true);
    setReceiptAcknowledgementError(null);

    try {
      await posApi.acknowledgeReceipt(receipt.order_id);
      setReceipt(null);
      setCompletedSale(null);
      setCart([]);
      setCartError(null);
      setCheckoutAttempt(null);
      setCardCheckoutAttempt(null);
      setCardCheckout(null);
      setIsAwaitingCardWebhook(false);
      setCardStatusError(null);
      setCheckoutError(null);
      setSelectedProductId(null);
      setSelectedPaymentMethod("CASH");
      setSearchTerm("");
      setCopyStatus(null);
    } catch (error: unknown) {
      setReceiptAcknowledgementError(
        getErrorMessage(
          error,
          "The receipt could not be acknowledged. The sale remains completed.",
        ),
      );
    } finally {
      receiptAcknowledgementInProgressRef.current = false;
      setIsAcknowledgingReceipt(false);
    }
  }, [receipt]);

  useEffect(() => {
    if (!receipt) return;

    const timeoutId = window.setTimeout(
      () => void finishReceipt(),
      RECEIPT_DISPLAY_DURATION_MS,
    );

    return () => window.clearTimeout(timeoutId);
  }, [finishReceipt, receipt]);

  async function handleCopyClaimCode(): Promise<void> {
    if (!receipt) return;

    setCopyStatus(null);
    try {
      if (!navigator.clipboard?.writeText) {
        throw new Error("Clipboard access is unavailable in this browser.");
      }

      await navigator.clipboard.writeText(receipt.claim_code);
      setCopyStatus("Claim code copied.");
    } catch (error: unknown) {
      setCopyStatus(
        getErrorMessage(
          error,
          "The claim code could not be copied. Select and copy it manually.",
        ),
      );
    }
  }

  if (session?.kind !== "staff") return null;

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Half Moon · Staff terminal</p>
          <h1 className={styles.heading}>Point of sale</h1>
          <p className={styles.staffIdentity}>
            Signed in as {session.profile.email} ({session.profile.role})
          </p>
        </div>
        <div className={styles.headerActions}>
          <Link className={styles.secondaryButton} to="/pos/orders">
            Order operations
          </Link>
          {logoutError && (
            <p className={styles.error} role="alert">
              {logoutError}
            </p>
          )}
          <button
            className={styles.secondaryButton}
            type="button"
            onClick={() => void handleLogout()}
            disabled={isLoggingOut}
          >
            {isLoggingOut ? "Signing out..." : "Sign out"}
          </button>
        </div>
      </header>

      {completedSale ? (
        <section
          className={styles.receiptPanel}
          aria-labelledby="completed-sale-heading"
          aria-live="polite"
        >
          <p className={styles.eyebrow}>
            {completedSale.payment_method === "CASH"
              ? "Cash sale complete"
              : "Card sale complete"}
          </p>
          <h2 id="completed-sale-heading" className={styles.sectionHeading}>
            Sale completed
          </h2>
          <dl className={styles.saleDetails}>
            <div>
              <dt>Order number</dt>
              <dd>{completedSale.order_number}</dd>
            </div>
            <div>
              <dt>Total</dt>
              <dd>{formatEuroPrice(completedSale.total_gross)}</dd>
            </div>
          </dl>

          {isLoadingReceipt && (
            <p className={styles.status} role="status">
              Loading the receipt...
            </p>
          )}

          {receiptError && (
            <div className={styles.errorPanel} role="alert">
              <p>{receiptError}</p>
              <button
                className={styles.secondaryButton}
                type="button"
                onClick={() => void loadReceipt(completedSale.order_id)}
                disabled={isLoadingReceipt}
              >
                {isLoadingReceipt ? "Loading..." : "Retry receipt"}
              </button>
            </div>
          )}

          {receipt && (
            <div className={styles.claimCodePanel}>
              <h3 className={styles.subheading}>Customer claim code</h3>
              <p className={styles.claimCode}>{receipt.claim_code}</p>
              <button
                className={styles.secondaryButton}
                type="button"
                onClick={() => void handleCopyClaimCode()}
                disabled={isAcknowledgingReceipt}
              >
                Copy claim code
              </button>
              {copyStatus && (
                <p className={styles.status} role="status">
                  {copyStatus}
                </p>
              )}
              <p className={styles.receiptNote}>
                A new sale will open automatically after the receipt is
                acknowledged.
              </p>
              {receiptAcknowledgementError && (
                <div className={styles.errorPanel} role="alert">
                  <p>{receiptAcknowledgementError}</p>
                  <button
                    className={styles.secondaryButton}
                    type="button"
                    onClick={() => void finishReceipt()}
                    disabled={isAcknowledgingReceipt}
                  >
                    {isAcknowledgingReceipt
                      ? "Finishing sale..."
                      : "Retry receipt acknowledgement"}
                  </button>
                </div>
              )}
            </div>
          )}
        </section>
      ) : (
        <>
          <p className={styles.intro}>
            Cash or card checkout · Anonymous sale · Prices and stock are
            confirmed by the server.
          </p>
          <div className={styles.terminalLayout}>
            <section
              className={styles.catalogPanel}
              aria-labelledby="catalog-heading"
            >
              <div className={styles.panelHeading}>
                <div>
                  <p className={styles.eyebrow}>Find an item</p>
                  <h2 id="catalog-heading" className={styles.sectionHeading}>
                    Product catalog
                  </h2>
                </div>
              </div>

              <label className={styles.searchField}>
                <span>Search products</span>
                <input
                  type="search"
                  value={searchTerm}
                  onChange={(event) => setSearchTerm(event.target.value)}
                  placeholder="Search by name or category"
                  disabled={cartIsLocked}
                />
              </label>

              {isLoadingCatalog && (
                <p className={styles.status} role="status">
                  Loading products...
                </p>
              )}
              {catalogError && (
                <div className={styles.errorPanel} role="alert">
                  <p>{catalogError}</p>
                  <button
                    className={styles.secondaryButton}
                    type="button"
                    onClick={() =>
                      setCatalogLoadAttempt((attempt) => attempt + 1)
                    }
                    disabled={isLoadingCatalog}
                  >
                    Retry catalog
                  </button>
                </div>
              )}
              {!isLoadingCatalog && !catalogError && (
                <div className={styles.productList}>
                  {filteredProducts.length === 0 ? (
                    <p className={styles.emptyState}>
                      No products match this search.
                    </p>
                  ) : (
                    filteredProducts.map((product) => {
                      const imageUrl =
                        product.variants.find(
                          (variant) => variant.image_url,
                        )?.image_url ?? product.image_url;

                      return (
                        <button
                          className={`${styles.productCard} ${
                            selectedProductId === product.id
                              ? styles.selectedProduct
                              : ""
                          }`}
                          type="button"
                          key={product.id}
                          onClick={() => {
                            setSelectedProductId(product.id);
                            setProductDetailLoadAttempt(0);
                          }}
                          disabled={cartIsLocked}
                          aria-pressed={selectedProductId === product.id}
                        >
                          {imageUrl && (
                            <img
                              className={styles.productImage}
                              src={imageUrl}
                              alt=""
                            />
                          )}
                          <span className={styles.productCardText}>
                            <span className={styles.productName}>
                              {product.name}
                            </span>
                            <span className={styles.mutedText}>
                              {displayCategory(product.category)}
                            </span>
                          </span>
                        </button>
                      );
                    })
                  )}
                </div>
              )}

              {selectedProduct && (
                <div className={styles.productDetail}>
                  <p className={styles.eyebrow}>Choose a variant</p>
                  <h3 className={styles.subheading}>
                    {selectedProduct.name}
                  </h3>
                  {isLoadingProductDetail && (
                    <p className={styles.status} role="status">
                      Loading variants and stock...
                    </p>
                  )}
                  {productDetailError && (
                    <div className={styles.errorPanel} role="alert">
                      <p>{productDetailError}</p>
                      <button
                        className={styles.secondaryButton}
                        type="button"
                        onClick={() =>
                          setProductDetailLoadAttempt((attempt) => attempt + 1)
                        }
                        disabled={isLoadingProductDetail}
                      >
                        Retry variants
                      </button>
                    </div>
                  )}
                  {productDetail && (
                    <div className={styles.variantList}>
                      {productDetail.variants.map((variant) => (
                        <article
                          className={styles.variantCard}
                          key={variant.id}
                        >
                          <div className={styles.variantDetails}>
                            <p className={styles.variantName}>
                              {getVariantDescription(
                                variant,
                                productDetail.category,
                              )}
                            </p>
                            <p className={styles.mutedText}>
                              {getLocationSummary(variant)}
                            </p>
                            <p className={styles.variantPrice}>
                              {formatEuroPrice(variant.retail_price)}
                            </p>
                          </div>
                          <button
                            className={styles.addButton}
                            type="button"
                            onClick={() =>
                              addVariantToCart(
                                productDetail.name,
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
                </div>
              )}
            </section>

            <section
              className={styles.cartPanel}
              aria-labelledby="cart-heading"
            >
              <p className={styles.eyebrow}>Current sale</p>
              <h2 id="cart-heading" className={styles.sectionHeading}>
                Basket
              </h2>

              {cart.length === 0 ? (
                <p className={styles.emptyState}>The basket is empty.</p>
              ) : (
                <ul className={styles.cartList}>
                  {cart.map((cartLine) => (
                    <li
                      className={styles.cartLine}
                      key={cartLine.variant.id}
                    >
                      <div className={styles.cartLineDetails}>
                        <p className={styles.productName}>
                          {cartLine.productName}
                        </p>
                        <p className={styles.mutedText}>
                          {getVariantDescription(
                            cartLine.variant,
                            cartLine.productCategory,
                          )}
                        </p>
                        <p className={styles.mutedText}>
                          {formatEuroPrice(cartLine.variant.retail_price)} each
                        </p>
                      </div>
                      <div className={styles.quantityControls}>
                        <button
                          className={styles.quantityButton}
                          type="button"
                          onClick={() =>
                            setCartLineQuantity(
                              cartLine.variant.id,
                              cartLine.quantity - 1,
                            )
                          }
                          disabled={cartIsLocked}
                          aria-label={`Remove one ${cartLine.productName}`}
                        >
                          -
                        </button>
                        <span aria-live="polite">{cartLine.quantity}</span>
                        <button
                          className={styles.quantityButton}
                          type="button"
                          onClick={() =>
                            setCartLineQuantity(
                              cartLine.variant.id,
                              cartLine.quantity + 1,
                            )
                          }
                          disabled={
                            cartIsLocked ||
                            cartLine.quantity >=
                              cartLine.variant.available_stock
                          }
                          aria-label={`Add one ${cartLine.productName}`}
                        >
                          +
                        </button>
                      </div>
                      <p className={styles.lineTotal}>
                        {formatCartLineTotal(cartLine)}
                      </p>
                    </li>
                  ))}
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

              {!cardCheckout && (
                <fieldset
                  className={styles.paymentMethodGroup}
                  disabled={cartIsLocked}
                >
                  <legend>Payment method</legend>
                  <label>
                    <input
                      type="radio"
                      name="pos-payment-method"
                      value="CASH"
                      checked={selectedPaymentMethod === "CASH"}
                      onChange={() => setSelectedPaymentMethod("CASH")}
                    />
                    Cash
                  </label>
                  <label>
                    <input
                      type="radio"
                      name="pos-payment-method"
                      value="CARD"
                      checked={selectedPaymentMethod === "CARD"}
                      onChange={() => setSelectedPaymentMethod("CARD")}
                    />
                    Card
                  </label>
                </fieldset>
              )}

              <div className={styles.totalRow}>
                <span>Total</span>
                <strong>
                  {cartTotalCents === null
                    ? "Price unavailable"
                    : formatEuroCents(cartTotalCents)}
                </strong>
              </div>
              {checkoutAttempt && (
                <p className={styles.mutedText}>
                  Retry will use the original basket and idempotency key.
                </p>
              )}
              {cardCheckoutAttempt && !cardCheckout && (
                <p className={styles.mutedText}>
                  Retry will use the original basket and idempotency key.
                </p>
              )}
              {!cardCheckout && selectedPaymentMethod === "CASH" && (
                <button
                  className={styles.checkoutButton}
                  type="button"
                  onClick={() => void handleCashCheckout()}
                  disabled={
                    cart.length === 0 ||
                    cartTotalCents === null ||
                    isSubmitting ||
                    completedSale !== null
                  }
                >
                  {isSubmitting
                    ? "Completing cash sale..."
                    : checkoutAttempt
                      ? "Retry same sale"
                      : "Complete cash sale"}
                </button>
              )}
              {!cardCheckout && selectedPaymentMethod === "CARD" && (
                <>
                  {!stripePublishableKey && (
                    <p className={styles.error} role="alert">
                      Card checkout needs the public Stripe setting
                      VITE_STRIPE_PUBLISHABLE_KEY. Use a test-mode publishable
                      key for local acceptance; do not use a Stripe secret key.
                    </p>
                  )}
                  <button
                    className={styles.checkoutButton}
                    type="button"
                    onClick={() => void handleCardCheckout()}
                    disabled={
                      cart.length === 0 ||
                      cartTotalCents === null ||
                      isSubmitting ||
                      completedSale !== null ||
                      !stripePublishableKey
                    }
                  >
                    {isSubmitting
                      ? "Starting card payment..."
                      : cardCheckoutAttempt
                        ? "Retry starting card payment"
                        : "Start card payment"}
                  </button>
                </>
              )}
              {cardCheckout && (
                <section
                  className={styles.cardPaymentPanel}
                  aria-labelledby="card-payment-heading"
                  aria-live="polite"
                >
                  <h3
                    id="card-payment-heading"
                    className={styles.subheading}
                  >
                    Card payment
                  </h3>
                  <p className={styles.mutedText}>
                    Order {cardCheckout.order_number} ·{" "}
                    {formatEuroPrice(cardCheckout.total_gross)}
                  </p>
                  <p className={styles.mutedText}>
                    A declined payment can be retried until the three-minute
                    reservation expires. This screen has no cancel action; if
                    left unpaid, the backend releases stock at expiry.
                  </p>
                  {isAwaitingCardWebhook && (
                    <p className={styles.status} role="status">
                      {hasExpiredCardReservation
                        ? "The reservation expired, but the payment outcome is unresolved. Do not start another sale. If this persists, reconcile the Stripe payment before proceeding."
                        : "Payment submitted. Waiting for the backend to confirm the signed Stripe event..."}
                    </p>
                  )}
                  {cardStatusError && (
                    <p className={styles.error} role="alert">
                      {cardStatusError}
                    </p>
                  )}
                  {stripePromise && (
                    <Elements stripe={stripePromise}>
                      <POSCardPaymentForm
                        clientSecret={
                          cardCheckout.payment_intent_client_secret
                        }
                        totalGross={cardCheckout.total_gross}
                        isAwaitingWebhook={isAwaitingCardWebhook}
                        onPaymentConfirmationStarted={() =>
                          setIsAwaitingCardWebhook(true)
                        }
                      />
                    </Elements>
                  )}
                </section>
              )}
            </section>
          </div>
        </>
      )}
    </main>
  );
}
