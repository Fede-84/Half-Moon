import axios from "axios";
import { useEffect, useRef, useState, type FormEvent } from "react";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import {
  ordersApi,
  type CreateOrderRequest,
  type FulfillmentMethod,
  type GuestCreateOrderRequest,
  type ShippingAddressRequest,
} from "../../api/orders";
import { formatApiErrorDetail } from "../../api/errors";
import { useCart } from "../../cart/CartContext";
import { createCollectionReturnHref } from "../../collectionFilters";
import {
  displayColor,
  displaySize,
  formatEuroCents,
  formatEuroPrice,
  shouldDisplaySize,
} from "../../catalogDisplay";
import { useAuth } from "../auth/AuthProvider";
import styles from "./CheckoutPage.module.css";

const TEST_SHIPPING_ADDRESS = {
  recipientName: "Test Shopper",
  addressLine1: "1 Test Street",
  addressLine2: "",
  city: "Test City",
  region: "Test Region",
  postalCode: "00000",
  countryCode: "IT",
};

function decimalPriceToCents(price: string): bigint | null {
  const priceParts = /^(\d+)(?:\.(\d{1,2}))?$/.exec(price);
  if (!priceParts) return null;

  return (
    BigInt(priceParts[1]) * 100n +
    BigInt((priceParts[2] ?? "").padEnd(2, "0"))
  );
}

function getCheckoutErrorMessage(error: unknown): string {
  const fallback =
    "We could not start checkout. Please check your details and try again.";

  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    return formatApiErrorDetail(error.response?.data?.detail, fallback);
  }

  if (error instanceof Error) return error.message;

  return fallback;
}

function optionalValue(value: string): string | undefined {
  const trimmedValue = value.trim();
  return trimmedValue.length > 0 ? trimmedValue : undefined;
}

function getDeliveryRecipientMessage(
  recipientIsBuyer: boolean,
  buyerName: string,
  recipientName: string,
): string {
  if (recipientIsBuyer) {
    return buyerName ? "Delivering to you" : "Enter your name for delivery.";
  }

  const trimmedRecipientName = recipientName.trim();
  if (!trimmedRecipientName) {
    return "Enter the recipient's name for delivery.";
  }

  return `Delivering to ${trimmedRecipientName}`;
}

export function CheckoutPage() {
  const [searchParams] = useSearchParams();
  const {
    items,
    persistenceError,
    markCheckoutPending,
    cancelCheckout,
  } = useCart();
  const continueShoppingHref = createCollectionReturnHref(searchParams);
  const { session, status } = useAuth();
  const [email, setEmail] = useState("");
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [fulfillmentMethod, setFulfillmentMethod] =
    useState<FulfillmentMethod>("SHIPPING");
  const [recipientIsBuyer, setRecipientIsBuyer] = useState(true);
  const [recipientName, setRecipientName] = useState(
    TEST_SHIPPING_ADDRESS.recipientName,
  );
  const [addressLine1, setAddressLine1] = useState(
    TEST_SHIPPING_ADDRESS.addressLine1,
  );
  const [addressLine2, setAddressLine2] = useState(
    TEST_SHIPPING_ADDRESS.addressLine2,
  );
  const [city, setCity] = useState(TEST_SHIPPING_ADDRESS.city);
  const [region, setRegion] = useState(TEST_SHIPPING_ADDRESS.region);
  const [postalCode, setPostalCode] = useState(
    TEST_SHIPPING_ADDRESS.postalCode,
  );
  const [countryCode, setCountryCode] = useState(
    TEST_SHIPPING_ADDRESS.countryCode,
  );
  const [phone, setPhone] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const isSubmittingRef = useRef(false);
  const idempotencyRef = useRef<{ fingerprint: string; key: string } | null>(
    null,
  );

  const subtotalCents = items.reduce<bigint | null>((subtotal, item) => {
    const unitPriceCents = decimalPriceToCents(item.retailPrice);
    if (subtotal === null || unitPriceCents === null) return null;

    return subtotal + unitPriceCents * BigInt(item.quantity);
  }, 0n);

  const customerSession = session?.kind === "customer" ? session : null;
  const customerBuyerName = customerSession
    ? [
        customerSession.profile.first_name,
        customerSession.profile.last_name,
      ]
        .filter(
          (namePart): namePart is string =>
            typeof namePart === "string" && namePart.trim().length > 0,
        )
        .join(" ")
    : "";
  const guestBuyerName = [firstName.trim(), lastName.trim()]
    .filter((namePart) => namePart.length > 0)
    .join(" ");
  const buyerName = customerSession ? customerBuyerName : guestBuyerName;
  const guestNameRequired =
    !customerSession &&
    (fulfillmentMethod === "STORE_PICKUP" ||
      (fulfillmentMethod === "SHIPPING" && recipientIsBuyer));

  if (items.length === 0) {
    return <Navigate to="/" replace />;
  }

  if (status === "loading") {
    return (
      <main className={styles.page}>
        <p role="status">Checking your session…</p>
      </main>
    );
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isSubmittingRef.current) return;

    isSubmittingRef.current = true;
    setIsSubmitting(true);
    setErrorMessage(null);

    try {
      if (!customerSession && !email.trim()) {
        setErrorMessage("Enter your email address to continue.");
        return;
      }

      if (guestNameRequired && (!firstName.trim() || !lastName.trim())) {
        setErrorMessage(
          "Enter your first and last name so the order can be identified.",
        );
        return;
      }

      const addressRecipientName =
        recipientIsBuyer && buyerName ? buyerName : recipientName.trim();

      if (
        fulfillmentMethod === "SHIPPING" &&
        (!addressRecipientName ||
          !addressLine1.trim() ||
          !city.trim() ||
          !postalCode.trim() ||
          !/^[A-Za-z]{2}$/.test(countryCode.trim()))
      ) {
        setErrorMessage(
          "Complete the required shipping recipient and address fields.",
        );
        return;
      }

      const shippingAddress: ShippingAddressRequest | undefined =
        fulfillmentMethod === "SHIPPING"
          ? {
              recipient_name: addressRecipientName,
              address_line_1: addressLine1.trim(),
              ...(optionalValue(addressLine2)
                ? { address_line_2: optionalValue(addressLine2) }
                : {}),
              city: city.trim(),
              ...(optionalValue(region)
                ? { region: optionalValue(region) }
                : {}),
              postal_code: postalCode.trim(),
              country_code: countryCode.trim().toUpperCase(),
              ...(optionalValue(phone) ? { phone: optionalValue(phone) } : {}),
            }
          : undefined;

      const customerRequest: CreateOrderRequest = {
        lines: items.map((item) => ({
          variant_id: item.variantId,
          quantity: item.quantity,
        })),
        fulfillment_method: fulfillmentMethod,
        ...(shippingAddress ? { shipping_address: shippingAddress } : {}),
      };

      const checkoutRequest = customerSession
        ? { kind: "customer" as const, body: customerRequest }
        : {
            kind: "guest" as const,
            body: {
              ...customerRequest,
              email: email.trim(),
              ...(optionalValue(firstName)
                ? { first_name: optionalValue(firstName) }
                : {}),
              ...(optionalValue(lastName)
                ? { last_name: optionalValue(lastName) }
                : {}),
              recipient_is_buyer:
                fulfillmentMethod === "STORE_PICKUP" || recipientIsBuyer,
            } satisfies GuestCreateOrderRequest,
          };

      const fingerprint = JSON.stringify(checkoutRequest);
      const previousRequest = idempotencyRef.current;
      const idempotencyKey =
        previousRequest?.fingerprint === fingerprint
          ? previousRequest.key
          : crypto.randomUUID();

      idempotencyRef.current = { fingerprint, key: idempotencyKey };

      const order =
        checkoutRequest.kind === "customer"
          ? await ordersApi.createCustomerOrder(
              checkoutRequest.body,
              idempotencyKey,
            )
          : await ordersApi.createGuestOrder(
              checkoutRequest.body,
              idempotencyKey,
            );

      const checkoutUrl = new URL(order.checkout_url);
      if (checkoutUrl.protocol !== "https:") {
        throw new Error("The secure payment page could not be opened.");
      }

      markCheckoutPending();
      window.location.assign(checkoutUrl.href);
    } catch (error: unknown) {
      cancelCheckout();
      setErrorMessage(getCheckoutErrorMessage(error));
    } finally {
      isSubmittingRef.current = false;
      setIsSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <Link
          className={styles.backLink}
          to={continueShoppingHref}
          state={{ scrollToCollectionImmediately: true }}
        >
          Continue shopping
        </Link>
        <p className={styles.eyebrow}>Half Moon checkout</p>
      </header>

      <div className={styles.layout}>
        <section className={styles.formCard} aria-labelledby="checkout-heading">
          <form onSubmit={handleSubmit}>
          <h1 className={styles.heading} id="checkout-heading">
            Checkout
          </h1>
          <p className={styles.intro}>
            Your order and current stock will be checked securely before
            payment.
          </p>

          {fulfillmentMethod === "SHIPPING" && (
            <p className={styles.testAddressNotice} role="note">
              <strong>Demo address:</strong> This prefilled shipping address
              is fictional. Orders submitted with it may still be recorded and
              affect inventory; do not use it for a real delivery.
            </p>
          )}

          {customerSession ? (
            <p className={styles.accountNotice}>
              Signed in as <strong>{customerSession.profile.email}</strong>
            </p>
          ) : (
            <>
              <p className={styles.signInPrompt}>
                Already have a customer account?{" "}
                <Link to="/login" state={{ returnTo: "/checkout" }}>
                  Sign in to continue
                </Link>
              </p>
              <section
                className={styles.section}
                aria-labelledby="contact-heading"
              >
                <h2 className={styles.sectionHeading} id="contact-heading">
                  Contact details
                </h2>
                <label className={styles.field}>
                  <span>Email address</span>
                  <input
                    className={styles.input}
                    autoComplete="email"
                    type="email"
                    name="email"
                    maxLength={254}
                    required
                    value={email}
                    onChange={(changeEvent) => setEmail(changeEvent.target.value)}
                    disabled={isSubmitting}
                  />
                </label>
                <div className={styles.fieldGrid}>
                  <label className={styles.field}>
                    <span>
                      First name{guestNameRequired ? "" : " (optional)"}
                    </span>
                    <input
                      className={styles.input}
                      autoComplete="given-name"
                      type="text"
                      name="first_name"
                      maxLength={100}
                      required={guestNameRequired}
                      value={firstName}
                      onChange={(changeEvent) =>
                        setFirstName(changeEvent.target.value)
                      }
                      disabled={isSubmitting}
                    />
                  </label>
                  <label className={styles.field}>
                    <span>
                      Last name{guestNameRequired ? "" : " (optional)"}
                    </span>
                    <input
                      className={styles.input}
                      autoComplete="family-name"
                      type="text"
                      name="last_name"
                      maxLength={100}
                      required={guestNameRequired}
                      value={lastName}
                      onChange={(changeEvent) =>
                        setLastName(changeEvent.target.value)
                      }
                      disabled={isSubmitting}
                    />
                  </label>
                </div>
              </section>
            </>
          )}

            <fieldset className={styles.optionGroup} disabled={isSubmitting}>
              <legend className={styles.sectionHeading}>
                How would you like to receive your order?
              </legend>
              <label className={styles.option}>
                <input
                  type="radio"
                  name="fulfillment_method"
                  value="SHIPPING"
                  checked={fulfillmentMethod === "SHIPPING"}
                  onChange={() => setFulfillmentMethod("SHIPPING")}
                />
                <span>Shipping — free</span>
              </label>
              <label className={styles.option}>
                <input
                  type="radio"
                  name="fulfillment_method"
                  value="STORE_PICKUP"
                  checked={fulfillmentMethod === "STORE_PICKUP"}
                  onChange={() => setFulfillmentMethod("STORE_PICKUP")}
                />
                <span>Store pickup</span>
              </label>
            </fieldset>

            {fulfillmentMethod === "SHIPPING" && (
              <>
                <fieldset className={styles.optionGroup} disabled={isSubmitting}>
                  <legend className={styles.sectionHeading}>
                    Who receives the order?
                  </legend>
                  <label className={styles.option}>
                    <input
                      type="radio"
                      name="recipient_is_buyer"
                      checked={recipientIsBuyer}
                      onChange={() => setRecipientIsBuyer(true)}
                    />
                    <span>Me</span>
                  </label>
                  <label className={styles.option}>
                    <input
                      type="radio"
                      name="recipient_is_buyer"
                      checked={!recipientIsBuyer}
                      onChange={() => setRecipientIsBuyer(false)}
                    />
                    <span>Someone else</span>
                  </label>
                </fieldset>

                <section
                  className={styles.section}
                  aria-labelledby="address-heading"
                >
                  <h2 className={styles.sectionHeading} id="address-heading">
                    Shipping address
                  </h2>
                  <p className={styles.accountNotice}>
                    {getDeliveryRecipientMessage(
                      recipientIsBuyer,
                      buyerName,
                      recipientName,
                    )}
                  </p>
                  {(!recipientIsBuyer || !buyerName) && (
                    <label className={styles.field}>
                      <span>Name on delivery</span>
                      <input
                        className={styles.input}
                        autoComplete="name"
                        type="text"
                        name="recipient_name"
                        maxLength={200}
                        required
                        value={recipientName}
                        onChange={(changeEvent) =>
                          setRecipientName(changeEvent.target.value)
                        }
                        disabled={isSubmitting}
                      />
                    </label>
                  )}
                  <div className={styles.addressGrid}>
                    <label className={styles.field}>
                      <span>Address</span>
                      <input
                        className={styles.input}
                        autoComplete="address-line1"
                        type="text"
                        name="address_line_1"
                        maxLength={200}
                        required
                        value={addressLine1}
                        onChange={(changeEvent) =>
                          setAddressLine1(changeEvent.target.value)
                        }
                        disabled={isSubmitting}
                      />
                    </label>
                    <label className={styles.field}>
                      <span>Address line 2 (optional)</span>
                      <input
                        className={styles.input}
                        autoComplete="address-line2"
                        type="text"
                        name="address_line_2"
                        maxLength={200}
                        value={addressLine2}
                        onChange={(changeEvent) =>
                          setAddressLine2(changeEvent.target.value)
                        }
                        disabled={isSubmitting}
                      />
                    </label>
                    <label className={styles.field}>
                      <span>City</span>
                      <input
                        className={styles.input}
                        autoComplete="address-level2"
                        type="text"
                        name="city"
                        maxLength={100}
                        required
                        value={city}
                        onChange={(changeEvent) =>
                          setCity(changeEvent.target.value)
                        }
                        disabled={isSubmitting}
                      />
                    </label>
                    <label className={styles.field}>
                      <span>Region (optional)</span>
                      <input
                        className={styles.input}
                        autoComplete="address-level1"
                        type="text"
                        name="region"
                        maxLength={100}
                        value={region}
                        onChange={(changeEvent) =>
                          setRegion(changeEvent.target.value)
                        }
                        disabled={isSubmitting}
                      />
                    </label>
                    <label className={styles.field}>
                      <span>Postal code</span>
                      <input
                        className={styles.input}
                        autoComplete="postal-code"
                        type="text"
                        name="postal_code"
                        maxLength={20}
                        required
                        value={postalCode}
                        onChange={(changeEvent) =>
                          setPostalCode(changeEvent.target.value)
                        }
                        disabled={isSubmitting}
                      />
                    </label>
                    <label className={styles.field}>
                      <span>Country code (2 letters)</span>
                      <input
                        className={styles.input}
                        type="text"
                        name="country_code"
                        minLength={2}
                        maxLength={2}
                        pattern="[A-Za-z]{2}"
                        title="Enter a two-letter country code"
                        required
                        value={countryCode}
                        onChange={(changeEvent) =>
                          setCountryCode(changeEvent.target.value)
                        }
                        disabled={isSubmitting}
                      />
                    </label>
                    <label className={styles.field}>
                      <span>Phone (optional)</span>
                      <input
                        className={styles.input}
                        autoComplete="tel"
                        type="tel"
                        name="phone"
                        maxLength={50}
                        value={phone}
                        onChange={(changeEvent) =>
                          setPhone(changeEvent.target.value)
                        }
                        disabled={isSubmitting}
                      />
                    </label>
                  </div>
                </section>
              </>
            )}

            {errorMessage && (
              <p className={styles.error} role="alert">
                {errorMessage}
              </p>
            )}

            {persistenceError && (
              <p className={styles.error} role="alert">
                {persistenceError}
              </p>
            )}

            {subtotalCents === null && (
              <p className={styles.error} role="alert">
                A cart price could not be displayed, so checkout is unavailable.
              </p>
            )}

            <button
              className={styles.submitButton}
              type="submit"
              disabled={isSubmitting || subtotalCents === null}
            >
              {isSubmitting
                ? "Preparing secure checkout…"
                : "Continue to secure payment"}
            </button>
          </form>
        </section>

        <aside className={styles.summaryCard} aria-labelledby="summary-heading">
          <h2 className={styles.sectionHeading} id="summary-heading">
            Your order
          </h2>
          <div className={styles.summaryItems}>
            {items.map((item) => (
              <article className={styles.summaryItem} key={item.variantId}>
                <img className={styles.itemImage} src={item.imageUrl} alt="" />
                <div className={styles.itemDetail}>
                  <h3 className={styles.itemTitle}>{item.productName}</h3>
                  <p>
                    {displayColor(item.color)}
                    {shouldDisplaySize(
                      item.productCategory,
                      item.sizeSystem,
                    )
                      ? ` · Size ${displaySize(item.sizeSystem, item.sizeLabel)}`
                      : ""}
                  </p>
                  <p>Quantity: {item.quantity}</p>
                </div>
                <strong>{formatEuroPrice(item.retailPrice)}</strong>
              </article>
            ))}
          </div>
          <p className={styles.subtotal}>
            <span>Subtotal</span>
            <strong>
              {subtotalCents === null
                ? "Price unavailable"
                : formatEuroCents(subtotalCents)}
            </strong>
          </p>
          <p className={styles.note}>
            Final price and availability are confirmed by the server before
            payment.
          </p>
        </aside>
      </div>
    </main>
  );
}

function CheckoutReturnPage({ success }: { success: boolean }) {
  const [searchParams] = useSearchParams();
  const location = useLocation();
  const navigate = useNavigate();
  const [checkoutSessionId] = useState(() => searchParams.get("session_id"));
  const [paymentStatus, setPaymentStatus] = useState<
    "checking" | "paid" | "pending" | "closed" | "unavailable"
  >(() => (checkoutSessionId ? "checking" : "unavailable"));
  const [paymentStatusError, setPaymentStatusError] = useState<string | null>(
    () =>
      checkoutSessionId
        ? null
        : "This return link did not include a checkout reference, so payment status could not be checked.",
  );
  const { session, status, sessionError } = useAuth();
  const { persistenceError, cancelCheckout, completeCheckout } = useCart();
  const continueShoppingHref = createCollectionReturnHref(searchParams);

  useEffect(() => {
    if (!success || !searchParams.has("session_id")) return;

    const sanitizedSearchParams = new URLSearchParams(searchParams);
    sanitizedSearchParams.delete("session_id");
    const sanitizedSearch = sanitizedSearchParams.toString();

    navigate(
      `${location.pathname}${sanitizedSearch ? `?${sanitizedSearch}` : ""}`,
      { replace: true, state: location.state },
    );
  }, [
    location.pathname,
    location.state,
    navigate,
    searchParams,
    success,
  ]);

  useEffect(() => {
    if (success) {
      completeCheckout();
      return;
    }

    cancelCheckout();
  }, [cancelCheckout, completeCheckout, success]);

  useEffect(() => {
    if (!success || status === "loading") {
      return;
    }

    let isActive = true;
    let timeoutId: number | undefined;
    const deadline = Date.now() + 30_000;

    if (!checkoutSessionId) {
      return () => {
        isActive = false;
      };
    }

    const checkPaymentStatus = async () => {
      try {
        const checkoutStatus =
          await ordersApi.getCheckoutPaymentStatus(checkoutSessionId);
        if (!isActive) return;

        if (checkoutStatus.payment_status === "PAID") {
          setPaymentStatus("paid");
          return;
        }

        if (
          ["FAILED", "PARTIALLY_REFUNDED", "REFUNDED"].includes(
            checkoutStatus.payment_status,
          ) ||
          ["CANCELLED", "EXPIRED"].includes(checkoutStatus.order_status)
        ) {
          setPaymentStatus("closed");
          return;
        }

        if (Date.now() >= deadline) {
          setPaymentStatus("pending");
          return;
        }

        timeoutId = window.setTimeout(() => {
          void checkPaymentStatus();
        }, 2_000);
      } catch (error) {
        if (!isActive) return;

        setPaymentStatus("unavailable");
        setPaymentStatusError(getCheckoutErrorMessage(error));
      }
    };

    void checkPaymentStatus();

    return () => {
      isActive = false;
      if (timeoutId !== undefined) {
        window.clearTimeout(timeoutId);
      }
    };
  }, [checkoutSessionId, status, success]);

  if (success && status === "loading") {
    return (
      <main className={styles.resultPage}>
        <section className={styles.resultCard}>
          <p className={styles.eyebrow}>Half Moon</p>
          <p role="status">
            Checking your account and payment before opening your orders…
          </p>
        </section>
      </main>
    );
  }

  if (success && session?.kind === "customer") {
    if (paymentStatus === "checking") {
      return (
        <main className={styles.resultPage}>
          <section className={styles.resultCard}>
            <p className={styles.eyebrow}>Half Moon</p>
            <p role="status">
              Confirming your payment before opening your account…
            </p>
          </section>
        </main>
      );
    }

    return (
      <Navigate
        to="/account"
        replace
        state={{ checkoutConfirmation: paymentStatus }}
      />
    );
  }

  return (
    <main className={styles.resultPage}>
      <section className={styles.resultCard}>
        <p className={styles.eyebrow}>Half Moon</p>
        <h1 className={styles.heading}>
          {success ? "Thank you" : "Checkout cancelled"}
        </h1>
        <p>
          {success
            ? sessionError
              ? `${sessionError} Sign in to view your order if needed.`
              : paymentStatus === "paid"
                ? "Thank you for your order, your payment has been successful."
                : paymentStatus === "checking"
                  ? "We’re checking Stripe’s payment confirmation."
                  : paymentStatus === "closed"
                    ? "This checkout was not confirmed as paid. Please check your account before trying again."
                    : paymentStatus === "unavailable"
                      ? "We couldn’t verify your payment status. Please check your account for the latest order information."
                      : "Your payment is still being confirmed. This page will check for up to 30 seconds; if it remains pending, check your account for the final status."
            : "Checkout was not completed. The inventory reservation may remain active until it expires."}
        </p>
        {success && paymentStatusError && (
          <p className={styles.error} role="alert">
            {paymentStatusError}
          </p>
        )}
        {persistenceError && (
          <p className={styles.error} role="alert">
            {persistenceError}
          </p>
        )}
        <Link
          className={styles.submitButton}
          to={continueShoppingHref}
          state={{ scrollToCollectionImmediately: true }}
        >
          Continue shopping
        </Link>
      </section>
    </main>
  );
}

export function CheckoutSuccessPage() {
  return <CheckoutReturnPage success />;
}

export function CheckoutCancelledPage() {
  return <CheckoutReturnPage success={false} />;
}
