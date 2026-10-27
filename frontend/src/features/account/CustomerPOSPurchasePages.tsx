import axios from "axios";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";
import {
  ordersApi,
  type CustomerOrderDetails,
  type CustomerOrderReturn,
  type CustomerOrderSummary,
} from "../../api/orders";
import { posApi, type POSReturnRequest } from "../../api/pos";
import {
  displayOrderSku,
  formatVariantDescription,
  formatEuroPrice,
} from "../../catalogDisplay";
import { formatApiErrorDetail } from "../../api/errors";
import styles from "./Account.module.css";

const POS_RETURN_REASON_LABELS = {
  DAMAGED: "Damaged",
  WRONG_SIZE: "Wrong size",
  WRONG_COLOUR: "Wrong colour",
  OTHER: "Other",
} as const;
const MAX_RETURN_REASON_DETAILS_LENGTH = 486;
const POS_RETURN_WINDOW_MS = 14 * 24 * 60 * 60 * 1000;

type POSReturnReason = keyof typeof POS_RETURN_REASON_LABELS;

function getErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    return formatApiErrorDetail(error.response?.data?.detail, fallback);
  }

  if (error instanceof Error) return error.message;
  return fallback;
}

function formatStatus(status: string): string {
  return status
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/\b\w/g, (character) => character.toUpperCase());
}

function formatDate(value: string | null): string {
  if (!value) return "Not recorded";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";

  return new Intl.DateTimeFormat("en-IE", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Rome",
  }).format(date);
}

function formatMoney(amount: string, currency: string): string {
  if (currency === "EUR") return formatEuroPrice(amount);
  return `${amount} ${currency}`;
}

function isPOSReturnReason(value: string): value is POSReturnReason {
  return Object.hasOwn(POS_RETURN_REASON_LABELS, value);
}

function isWithinPOSReturnWindow(order: CustomerOrderDetails): boolean {
  if (!order.paid_at) return false;
  const paidAt = new Date(order.paid_at).getTime();
  return (
    !Number.isNaN(paidAt) &&
    Date.now() <= paidAt + POS_RETURN_WINDOW_MS
  );
}

export function CustomerPOSPurchaseListPage() {
  const [orders, setOrders] = useState<CustomerOrderSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);

  useEffect(() => {
    let isCurrent = true;

    async function loadOrders(): Promise<void> {
      setIsLoading(true);
      setError(null);
      try {
        const customerOrders = await ordersApi.listCustomerPOSOrders();
        if (isCurrent) setOrders(customerOrders);
      } catch (loadError: unknown) {
        if (isCurrent) {
          setError(
            getErrorMessage(
              loadError,
              "Your POS purchases could not be loaded.",
            ),
          );
        }
      } finally {
        if (isCurrent) setIsLoading(false);
      }
    }

    void loadOrders();
    return () => {
      isCurrent = false;
    };
  }, [loadAttempt]);

  return (
    <section className={styles.section} aria-labelledby="pos-purchases-heading">
      <div className={styles.sectionHeader}>
        <div>
          <p className={styles.eyebrow}>Customer account</p>
          <h1 className={styles.heading} id="pos-purchases-heading">
            POS purchases
          </h1>
        </div>
        <div className={styles.detailActions}>
          <Link to="/account/claim-pos-order">Claim a POS purchase</Link>
          <Link to="/pos-demo">Try POS demo</Link>
        </div>
      </div>

      {isLoading && <p role="status">Loading POS purchases...</p>}
      {!isLoading && error && (
        <div className={styles.error} role="alert">
          <p>{error}</p>
          <button
            className={styles.textButton}
            type="button"
            onClick={() => setLoadAttempt((attempt) => attempt + 1)}
          >
            Try again
          </button>
        </div>
      )}
      {!isLoading && !error && orders.length === 0 && (
        <div className={styles.empty}>
          <p>You have no claimed POS purchases yet.</p>
          <p>
            <Link to="/pos-demo">Make a demo purchase</Link> or{" "}
            <Link to="/account/claim-pos-order">claim an existing purchase</Link>.
          </p>
        </div>
      )}
      {!isLoading && !error && orders.length > 0 && (
        <div className={styles.orderList}>
          {orders.map((order) => (
            <article className={styles.orderCard} key={order.order_id}>
              <div className={styles.orderHeading}>
                <div>
                  <h2>Order {order.order_number}</h2>
                  <p>
                    Purchased{" "}
                    <time dateTime={order.created_at}>
                      {formatDate(order.created_at)}
                    </time>
                  </p>
                </div>
                <Link
                  className={styles.orderLink}
                  to={`/account/pos-purchases/${encodeURIComponent(order.order_id)}`}
                >
                  View purchase
                </Link>
              </div>
              <dl className={styles.summaryGrid}>
                <div>
                  <dt>Order status</dt>
                  <dd>{formatStatus(order.order_status)}</dd>
                </div>
                <div>
                  <dt>Payment</dt>
                  <dd>{formatStatus(order.payment_status)}</dd>
                </div>
                <div>
                  <dt>Total</dt>
                  <dd>{formatMoney(order.total_gross, order.currency)}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

export function CustomerPOSPurchaseDetailPage() {
  const { orderId } = useParams();
  const [order, setOrder] = useState<CustomerOrderDetails | null>(null);
  const [returns, setReturns] = useState<CustomerOrderReturn[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [reasonCategory, setReasonCategory] = useState<POSReturnReason | "">("");
  const [reasonDetails, setReasonDetails] = useState("");
  const [selectedUnitIds, setSelectedUnitIds] = useState<string[]>([]);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [returnError, setReturnError] = useState<string | null>(null);
  const [returnMessage, setReturnMessage] = useState<string | null>(null);
  const submissionInProgressRef = useRef(false);

  useEffect(() => {
    if (!orderId) return;

    const requestedOrderId = orderId;
    let isCurrent = true;

    async function loadPurchase(): Promise<void> {
      setIsLoading(true);
      setLoadError(null);
      try {
        const [details, customerReturns] = await Promise.all([
          ordersApi.getCustomerOrderDetails(requestedOrderId),
          ordersApi.listCustomerOrderReturns(requestedOrderId),
        ]);
        if (details.channel !== "POS") {
          throw new Error("This purchase is not a POS order.");
        }
        if (isCurrent) {
          setOrder(details);
          setReturns(customerReturns);
        }
      } catch (error: unknown) {
        if (isCurrent) {
          setLoadError(
            getErrorMessage(error, "The POS purchase could not be loaded."),
          );
        }
      } finally {
        if (isCurrent) setIsLoading(false);
      }
    }

    void loadPurchase();
    return () => {
      isCurrent = false;
    };
  }, [loadAttempt, orderId]);

  function toggleSelectedUnit(unitId: string, isSelected: boolean): void {
    setSelectedUnitIds((currentIds) => {
      if (isSelected) {
        return currentIds.includes(unitId) ? currentIds : [...currentIds, unitId];
      }
      return currentIds.filter((currentId) => currentId !== unitId);
    });
  }

  async function handleSubmitReturn(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (!order || !orderId || submissionInProgressRef.current) return;

    const normalizedDetails = reasonDetails.trim();
    if (!isPOSReturnReason(reasonCategory)) {
      setReturnError("Choose a return reason.");
      return;
    }
    if (reasonCategory === "OTHER" && !normalizedDetails) {
      setReturnError("Add details when selecting Other.");
      return;
    }
    if (selectedUnitIds.length === 0) {
      setReturnError("Select at least one item to return.");
      return;
    }

    const reason = normalizedDetails
      ? `${POS_RETURN_REASON_LABELS[reasonCategory]}: ${normalizedDetails}`
      : POS_RETURN_REASON_LABELS[reasonCategory];
    const request: POSReturnRequest = {
      requested_outcome: "REFUND",
      reason,
      items: selectedUnitIds.map((unitId) => ({
        order_line_unit_id: unitId,
      })),
    };

    submissionInProgressRef.current = true;
    setIsSubmitting(true);
    setReturnError(null);
    setReturnMessage(null);
    try {
      const response = await posApi.processCustomerReturn(orderId, request);
      setReturnMessage(
        `Return completed. Simulated refund: ${formatMoney(
          response.amount,
          order.currency,
        )}.`,
      );
      setSelectedUnitIds([]);
      setReasonCategory("");
      setReasonDetails("");
    } catch (error: unknown) {
      setReturnError(
        getErrorMessage(
          error,
          "The return could not be completed. Please refresh and try again.",
        ),
      );
    } finally {
      submissionInProgressRef.current = false;
      setIsSubmitting(false);
      setLoadAttempt((attempt) => attempt + 1);
    }
  }

  if (isLoading) {
    return <p role="status">Loading POS purchase...</p>;
  }

  if (loadError || !order || !orderId) {
    return (
      <section className={styles.section} aria-labelledby="pos-detail-error">
        <div className={styles.detailHeader}>
          <h1 className={styles.heading} id="pos-detail-error">
            POS purchase unavailable
          </h1>
          <Link to="/account/pos-purchases">Back to POS purchases</Link>
        </div>
        <p className={styles.error} role="alert">
          {loadError ?? "POS purchase not found."}
        </p>
        <button
          className={styles.textButton}
          type="button"
          onClick={() => setLoadAttempt((attempt) => attempt + 1)}
        >
          Try again
        </button>
      </section>
    );
  }

  const selectableUnits = order.lines.flatMap((line) =>
    line.units.flatMap((unit, index) =>
      unit.already_returned
        ? []
        : [
            {
              unitId: unit.order_line_unit_id,
              description: `${line.product_name} · item ${index + 1} · ${formatVariantDescription(line.variant, " · ")}`,
            },
          ],
    ),
  );
  const isWithinReturnWindow = isWithinPOSReturnWindow(order);
  const canReturn =
    order.order_status === "COMPLETED" &&
    (order.payment_status === "PAID" ||
      order.payment_status === "PARTIALLY_REFUNDED") &&
    isWithinReturnWindow &&
    selectableUnits.length > 0;

  return (
    <section className={styles.section} aria-labelledby="pos-order-heading">
      <div className={styles.detailHeader}>
        <div>
          <p className={styles.eyebrow}>POS purchase</p>
          <h1 className={styles.heading} id="pos-order-heading">
            Order {order.order_number}
          </h1>
        </div>
        <Link to="/account/pos-purchases">Back to POS purchases</Link>
      </div>

      <section className={styles.lines} aria-labelledby="pos-lines-heading">
        <h2 id="pos-lines-heading">Items</h2>
        <ul className={styles.lineList}>
          {order.lines.map((line) => (
            <li className={styles.lineItem} key={line.order_line_id}>
              <div>
                <h3>{line.product_name}</h3>
                <p>{formatVariantDescription(line.variant, " · ")}</p>
                <p>
                  {line.quantity} item{line.quantity === 1 ? "" : "s"} · SKU{" "}
                  {displayOrderSku(line.sku, line.variant)}
                </p>
              </div>
              <dl className={styles.lineAmounts}>
                <div>
                  <dt>Unit price</dt>
                  <dd>{formatMoney(line.unit_price_gross, order.currency)}</dd>
                </div>
                <div>
                  <dt>Line total</dt>
                  <dd>{formatMoney(line.total_gross, order.currency)}</dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
        <p>
          Purchased{" "}
          <time dateTime={order.paid_at ?? order.created_at}>
            {formatDate(order.paid_at ?? order.created_at)}
          </time>
          {" · "}Total {formatMoney(order.total_gross, order.currency)}
        </p>
      </section>

      <section className={styles.returns} aria-labelledby="pos-returns-heading">
        <div>
          <h2 id="pos-returns-heading">Returns</h2>
          <p>
            Eligible returns are processed automatically with a simulated
            original-payment refund and restocking. The backend verifies the
            14-day policy and item eligibility when submitted.
          </p>
        </div>

        {returnMessage && (
          <p className={styles.returnNotice} role="status">
            {returnMessage}
          </p>
        )}
        {returnError && (
          <p className={styles.error} role="alert">
            {returnError}
          </p>
        )}

        {returns.length > 0 && (
          <div className={styles.returnHistory}>
            <h3>Return activity</h3>
            {returns.map((returnRecord) => (
              <article className={styles.returnCard} key={returnRecord.return_id}>
                <div className={styles.returnCardHeading}>
                  <p>
                    {formatStatus(returnRecord.status)} · Requested{" "}
                    <time dateTime={returnRecord.requested_at}>
                      {formatDate(returnRecord.requested_at)}
                    </time>
                  </p>
                  <p>{returnRecord.reason}</p>
                </div>
                {returnRecord.refunds.map((refund, index) => (
                  <p
                    className={styles.returnRefund}
                    key={`${returnRecord.return_id}-${index}`}
                  >
                    Simulated refund:{" "}
                    {formatMoney(refund.amount, order.currency)} (
                    {formatStatus(refund.status)})
                  </p>
                ))}
              </article>
            ))}
          </div>
        )}

        {order.order_status !== "COMPLETED" && (
          <p>Only completed POS purchases can be returned.</p>
        )}
        {order.order_status === "COMPLETED" &&
          order.payment_status !== "PAID" &&
          order.payment_status !== "PARTIALLY_REFUNDED" && (
            <p>This purchase has no remaining refundable payment balance.</p>
          )}
        {order.order_status === "COMPLETED" &&
          (order.payment_status === "PAID" ||
            order.payment_status === "PARTIALLY_REFUNDED") &&
          !isWithinReturnWindow && (
            <p>The 14-day POS return window has passed.</p>
          )}
        {order.order_status === "COMPLETED" &&
          (order.payment_status === "PAID" ||
            order.payment_status === "PARTIALLY_REFUNDED") &&
          isWithinReturnWindow &&
          selectableUnits.length === 0 && (
            <p>All items in this purchase have already been returned.</p>
          )}

        {canReturn && (
          <form
            className={styles.returnForm}
            onSubmit={(event) => void handleSubmitReturn(event)}
          >
            <fieldset disabled={isSubmitting}>
              <legend>Select items to return</legend>
              <div className={styles.returnChoices}>
                {selectableUnits.map((unit) => (
                  <label className={styles.returnChoice} key={unit.unitId}>
                    <input
                      type="checkbox"
                      checked={selectedUnitIds.includes(unit.unitId)}
                      onChange={(event) =>
                        toggleSelectedUnit(unit.unitId, event.target.checked)
                      }
                    />
                    <span>{unit.description}</span>
                  </label>
                ))}
              </div>
            </fieldset>

            <label className={styles.returnReason}>
              <span>Reason for return (required)</span>
              <select
                required
                value={reasonCategory}
                onChange={(event) => {
                  const value = event.target.value;
                  setReasonCategory(isPOSReturnReason(value) ? value : "");
                  setReturnError(null);
                }}
              >
                <option value="">Choose a reason</option>
                <option value="DAMAGED">Damaged</option>
                <option value="WRONG_SIZE">Wrong size</option>
                <option value="WRONG_COLOUR">Wrong colour</option>
                <option value="OTHER">Other</option>
              </select>
              <span className={styles.returnReasonHelp}>
                Add details if you choose Other.
              </span>
            </label>

            <label className={styles.returnReason}>
              <span>
                Additional details (
                {reasonCategory === "OTHER" ? "required" : "optional"})
              </span>
              <textarea
                maxLength={MAX_RETURN_REASON_DETAILS_LENGTH}
                required={reasonCategory === "OTHER"}
                value={reasonDetails}
                onChange={(event) => {
                  setReasonDetails(event.target.value);
                  setReturnError(null);
                }}
              />
              <span className={styles.returnReasonHelp}>
                {reasonDetails.length}/{MAX_RETURN_REASON_DETAILS_LENGTH}
              </span>
            </label>

            <button
              className={styles.button}
              type="submit"
              disabled={isSubmitting || selectedUnitIds.length === 0}
            >
              {isSubmitting ? "Processing return..." : "Process return"}
            </button>
          </form>
        )}
      </section>
    </section>
  );
}
