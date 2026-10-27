import axios from "axios";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  Link,
  Outlet,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  displayOrderSku,
  formatVariantDescription,
  formatEuroPrice,
} from "../../catalogDisplay";
import { formatApiErrorDetail } from "../../api/errors";
import {
  ordersApi,
  type CustomerOrderDetails,
  type CustomerOrderShipmentStatus,
  type CustomerOrderReturn,
  type CustomerOrderSummary,
} from "../../api/orders";
import { createCollectionReturnHref } from "../../collectionFilters";
import { useAuth } from "../auth/AuthProvider";
import styles from "./Account.module.css";

const CUSTOMER_RETURN_REASON_LABELS = {
  DAMAGED: "Damaged",
  WRONG_SIZE: "Wrong size",
  WRONG_COLOUR: "Wrong colour",
  OTHER: "Other",
} as const;

type CustomerReturnReason = keyof typeof CUSTOMER_RETURN_REASON_LABELS;

const MAX_RETURN_REASON_DETAILS_LENGTH = 486;

function isCustomerReturnReason(value: string): value is CustomerReturnReason {
  return Object.hasOwn(CUSTOMER_RETURN_REASON_LABELS, value);
}

function getOrderErrorMessage(error: unknown, fallback: string): string {
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

function formatOrderDate(value: string | null): string {
  if (!value) return "Not recorded";

  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Date unavailable";

  return new Intl.DateTimeFormat("en-IE", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Europe/Rome",
  }).format(date);
}

function formatOrderMoney(amount: string, currency: string): string {
  if (currency === "EUR") return formatEuroPrice(amount);

  return `${amount} ${currency}`;
}

export function AccountLayout() {
  const { session, logout } = useAuth();
  const [searchParams] = useSearchParams();
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const continueShoppingHref = createCollectionReturnHref(searchParams);

  async function handleLogout() {
    if (isLoggingOut) return;

    setIsLoggingOut(true);
    setLogoutError(null);

    try {
      await logout();
    } catch {
      setLogoutError("Sign out failed. Please try again.");
    } finally {
      setIsLoggingOut(false);
    }
  }

  if (session?.kind !== "customer") return null;

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <div className={styles.identity}>
          <Link className={styles.brand} to="/">
            Half Moon
          </Link>
          <p>Signed in as {session.profile.email}</p>
        </div>
        <div className={styles.headerActions}>
          <Link
            to={continueShoppingHref}
            state={{ scrollToCollectionImmediately: true }}
          >
            Continue shopping
          </Link>
          <Link to="/account/claim-pos-order">Claim a POS order</Link>
          <Link to="/account/pos-purchases">POS purchases</Link>
          <button
            className={styles.button}
            type="button"
            onClick={() => void handleLogout()}
            disabled={isLoggingOut}
          >
            {isLoggingOut ? "Signing out..." : "Sign out"}
          </button>
        </div>
      </header>

      {logoutError && (
        <p className={styles.error} role="alert">
          {logoutError}
        </p>
      )}

      <main className={styles.content}>
        <Outlet />
      </main>
    </div>
  );
}

export function CustomerOrderListPage() {
  const [orders, setOrders] = useState<CustomerOrderSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryCount, setRetryCount] = useState(0);

  useEffect(() => {
    let isCurrent = true;

    async function loadOrders() {
      try {
        const customerOrders = await ordersApi.listCustomerOrders();

        if (isCurrent) setOrders(customerOrders);
      } catch (loadError: unknown) {
        if (isCurrent) {
          setError(
            getOrderErrorMessage(
              loadError,
              "Your orders could not be loaded. Please try again.",
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
  }, [retryCount]);

  function retryLoadingOrders() {
    setError(null);
    setIsLoading(true);
    setRetryCount((currentCount) => currentCount + 1);
  }

  return (
    <section className={styles.section} aria-labelledby="orders-heading">
      <div className={styles.sectionHeader}>
        <p className={styles.eyebrow}>Customer account</p>
        <h1 className={styles.heading} id="orders-heading">
          Your orders
        </h1>
      </div>

      {isLoading && <p role="status">Loading your orders...</p>}

      {!isLoading && error && (
        <div className={styles.error} role="alert">
          <p>{error}</p>
          <button
            className={styles.textButton}
            type="button"
            onClick={retryLoadingOrders}
          >
            Try again
          </button>
        </div>
      )}

      {!isLoading && !error && orders.length === 0 && (
        <p className={styles.empty}>
          You do not have any E-Commerce orders yet.
        </p>
      )}

      {!isLoading && !error && orders.length > 0 && (
        <div className={styles.orderList}>
          {orders.map((order) => (
            <article className={styles.orderCard} key={order.order_id}>
              <div className={styles.orderHeading}>
                <div>
                  <h2>Order {order.order_number}</h2>
                  <p>
                    Placed{" "}
                    <time dateTime={order.created_at}>
                      {formatOrderDate(order.created_at)}
                    </time>
                  </p>
                </div>
                <Link
                  className={styles.orderLink}
                  to={`/account/orders/${encodeURIComponent(order.order_id)}`}
                >
                  View order
                </Link>
              </div>

              <dl className={styles.summaryGrid}>
                <div>
                  <dt>Order status</dt>
                  <dd>{formatStatus(order.order_status)}</dd>
                </div>
                <div>
                  <dt>Total</dt>
                  <dd>{formatOrderMoney(order.total_gross, order.currency)}</dd>
                </div>
              </dl>
            </article>
          ))}
        </div>
      )}
    </section>
  );
}

type CustomerOrderLoadResult =
  | {
      requestedOrderId: string;
      requestedRetryCount: number;
      kind: "loaded";
      order: CustomerOrderDetails;
      fulfillment: CustomerOrderShipmentStatus | null;
      fulfillmentError: string | null;
    }
  | {
      requestedOrderId: string;
      requestedRetryCount: number;
      kind: "error";
      message: string;
    };

function StatusDate({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  if (!value) return null;

  return (
    <div>
      <dt>{label}</dt>
      <dd>
        <time dateTime={value}>{formatOrderDate(value)}</time>
      </dd>
    </div>
  );
}

function getOrderLineForUnit(
  order: CustomerOrderDetails,
  orderLineUnitId: string,
) {
  return order.lines.find((line) =>
    line.units.some((unit) => unit.order_line_unit_id === orderLineUnitId),
  );
}

function CustomerReturnPanel({
  order,
  fulfillment,
}: {
  order: CustomerOrderDetails;
  fulfillment: CustomerOrderShipmentStatus | null;
}) {
  const [returnRecords, setReturnRecords] = useState<CustomerOrderReturn[]>([]);
  const [isLoadingReturns, setIsLoadingReturns] = useState(true);
  const [returnsLoadError, setReturnsLoadError] = useState<string | null>(null);
  const [returnStatusRefreshCount, setReturnStatusRefreshCount] = useState(0);
  const [selectedUnitIds, setSelectedUnitIds] = useState<string[]>([]);
  const [reasonCategory, setReasonCategory] = useState<
    CustomerReturnReason | ""
  >("");
  const [reasonDetails, setReasonDetails] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [processingReturnId, setProcessingReturnId] = useState<string | null>(
    null,
  );
  const [recoveryReturnId, setRecoveryReturnId] = useState<string | null>(null);
  const [returnError, setReturnError] = useState<string | null>(null);
  const [returnNotice, setReturnNotice] = useState<string | null>(null);
  const returnActionInProgress = useRef(false);

  useEffect(() => {
    let isCurrent = true;

    async function loadReturnRecords() {
      try {
        const customerReturns = await ordersApi.listCustomerOrderReturns(
          order.order_id,
        );
        if (isCurrent) {
          setReturnRecords(customerReturns);
          setReturnsLoadError(null);
        }
      } catch (error: unknown) {
        if (isCurrent) {
          setReturnsLoadError(
            getOrderErrorMessage(
              error,
              "Return status could not be loaded. Please try again.",
            ),
          );
        }
      } finally {
        if (isCurrent) setIsLoadingReturns(false);
      }
    }

    void loadReturnRecords();

    return () => {
      isCurrent = false;
    };
  }, [order.order_id, returnStatusRefreshCount]);

  const returnRecordUnitIds = new Set(
    returnRecords.flatMap((returnRecord) =>
      returnRecord.items.map((item) => item.order_line_unit_id),
    ),
  );
  const returnableUnitIds = new Set(
    order.lines.flatMap((line) =>
      line.units
        .filter(
          (unit) =>
            !unit.already_returned &&
            !returnRecordUnitIds.has(unit.order_line_unit_id),
        )
        .map((unit) => unit.order_line_unit_id),
    ),
  );
  const selectableUnits = order.lines.flatMap((line) =>
    line.units.flatMap((unit, unitIndex) => {
      if (
        unit.already_returned ||
        returnRecordUnitIds.has(unit.order_line_unit_id)
      ) {
        return [];
      }

      return [{ line, unit, unitNumber: unitIndex + 1 }];
    }),
  );
  const hasRefundablePayment =
    order.payment_status === "PAID" ||
    order.payment_status === "PARTIALLY_REFUNDED";
  const mayStartReturn =
    hasRefundablePayment &&
    fulfillment !== null &&
    fulfillment.order_status === order.order_status &&
    ((fulfillment.status === "DELIVERED" &&
      order.order_status === "DELIVERED") ||
      (fulfillment.status === "PICKED_UP" &&
        order.order_status === "PICKED_UP"));
  const isReturnOperationActive =
    isSubmitting || processingReturnId !== null;

  function toggleSelectedUnit(orderLineUnitId: string, isSelected: boolean) {
    setSelectedUnitIds((currentUnitIds) => {
      if (isSelected) {
        return currentUnitIds.includes(orderLineUnitId)
          ? currentUnitIds
          : [...currentUnitIds, orderLineUnitId];
      }

      return currentUnitIds.filter((unitId) => unitId !== orderLineUnitId);
    });
  }

  function scheduleReturnStatusRefresh() {
    setReturnsLoadError(null);
    setIsLoadingReturns(true);
    setReturnStatusRefreshCount((currentCount) => currentCount + 1);
  }

  async function handleSubmitReturn(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (returnActionInProgress.current) return;

    const selectedEligibleUnitIds = selectedUnitIds.filter((unitId) =>
      returnableUnitIds.has(unitId),
    );

    if (selectedEligibleUnitIds.length === 0) {
      setReturnError("Select at least one item to return.");
      return;
    }

    if (!reasonCategory) {
      setReturnError("Choose a reason for the return.");
      return;
    }

    const trimmedReasonDetails = reasonDetails.trim();

    if (reasonCategory === "OTHER" && !trimmedReasonDetails) {
      setReturnError("Add details when selecting Other.");
      return;
    }

    const trimmedReason = trimmedReasonDetails
      ? `${CUSTOMER_RETURN_REASON_LABELS[reasonCategory]}: ${trimmedReasonDetails}`
      : CUSTOMER_RETURN_REASON_LABELS[reasonCategory];

    returnActionInProgress.current = true;
    setIsSubmitting(true);
    setReturnError(null);
    setReturnNotice(null);

    let createdReturnId: string | null = null;

    try {
      const createdReturn = await ordersApi.createCustomerReturn(
        order.order_id,
        {
          requested_outcome: "REFUND",
          reason: trimmedReason,
          items: selectedEligibleUnitIds.map((orderLineUnitId) => ({
            order_line_unit_id: orderLineUnitId,
          })),
        },
      );
      createdReturnId = createdReturn.return_id;
      setRecoveryReturnId(createdReturn.return_id);

      const processedReturn = await ordersApi.processCustomerReturn(
        createdReturn.return_id,
      );
      setReturnNotice(
        `Return completed. A simulated refund of ${formatOrderMoney(
          processedReturn.amount,
          order.currency,
        )} was recorded.`,
      );
      setReturnError(null);
      setRecoveryReturnId(null);
      setSelectedUnitIds([]);
      setReasonCategory("");
      setReasonDetails("");
    } catch (error: unknown) {
      if (createdReturnId) {
        setRecoveryReturnId(createdReturnId);
        setReturnError(
          "The return request was saved, but processing could not be confirmed. Check its status below and resume processing if it is still requested.",
        );
      } else {
        setReturnError(
          getOrderErrorMessage(
            error,
            "The return request could not be submitted. Please try again.",
          ),
        );
      }
    } finally {
      returnActionInProgress.current = false;
      setIsSubmitting(false);
      scheduleReturnStatusRefresh();
    }
  }

  async function handleProcessReturn(returnId: string) {
    if (returnActionInProgress.current) return;

    returnActionInProgress.current = true;
    setProcessingReturnId(returnId);
    setReturnError(null);
    setReturnNotice(null);

    try {
      const processedReturn =
        await ordersApi.processCustomerReturn(returnId);
      setReturnNotice(
        `Return completed. A simulated refund of ${formatOrderMoney(
          processedReturn.amount,
          order.currency,
        )} was recorded.`,
      );
      setRecoveryReturnId(null);
    } catch (error: unknown) {
      setRecoveryReturnId(returnId);
      setReturnError(
        getOrderErrorMessage(
          error,
          "Return processing could not be confirmed. Check the status below and try again if it remains requested.",
        ),
      );
    } finally {
      returnActionInProgress.current = false;
      setProcessingReturnId(null);
      scheduleReturnStatusRefresh();
    }
  }

  const hasCurrentRecoveryReturn =
    recoveryReturnId !== null &&
    returnRecords.some(
      (returnRecord) => returnRecord.return_id === recoveryReturnId,
    );

  return (
    <section className={styles.returns} aria-labelledby="returns-heading">
      <div>
        <h2 id="returns-heading">Returns</h2>
        <p>
          Eligible returns are processed immediately. Refunds and restocking are
          simulated; no real payment-provider refund is sent.
        </p>
      </div>

      {isLoadingReturns && <p role="status">Checking return status...</p>}

      {returnsLoadError && (
        <div className={styles.error} role="alert">
          <p>{returnsLoadError}</p>
          <button
            className={styles.textButton}
            type="button"
            onClick={scheduleReturnStatusRefresh}
          >
            Retry status check
          </button>
        </div>
      )}

      {returnNotice && (
        <p className={styles.returnNotice} role="status">
          {returnNotice}
        </p>
      )}

      {returnError && (
        <p className={styles.error} role="alert">
          {returnError}
        </p>
      )}

      {!isLoadingReturns && !returnsLoadError && returnRecords.length > 0 && (
        <div className={styles.returnHistory}>
          <h3>Return activity</h3>
          {returnRecords.map((returnRecord) => (
            <article
              className={styles.returnCard}
              key={returnRecord.return_id}
            >
              <div className={styles.returnCardHeading}>
                <p>
                  {formatStatus(returnRecord.status)} · Requested{" "}
                  <time dateTime={returnRecord.requested_at}>
                    {formatOrderDate(returnRecord.requested_at)}
                  </time>
                </p>
                <p>{returnRecord.reason}</p>
              </div>
              <ul className={styles.returnItemList}>
                {returnRecord.items.map((item) => {
                  const matchingLine = getOrderLineForUnit(
                    order,
                    item.order_line_unit_id,
                  );

                  return (
                    <li
                      className={styles.returnHistoryItem}
                      key={item.order_line_unit_id}
                    >
                      {matchingLine && (
                        <img
                          className={styles.returnItemImage}
                          src={matchingLine.image_url}
                          alt=""
                        />
                      )}
                      <span>
                        {matchingLine
                          ? `${matchingLine.product_name}, ${formatVariantDescription(matchingLine.variant)}`
                          : "Order item"}
                      </span>
                    </li>
                  );
                })}
              </ul>
              {returnRecord.refunds.map((refund, refundIndex) => (
                <p
                  className={styles.returnRefund}
                  key={`${returnRecord.return_id}-${refundIndex}`}
                >
                  Simulated original-payment refund:{" "}
                  {formatOrderMoney(refund.amount, order.currency)} (
                  {formatStatus(refund.status)})
                </p>
              ))}
              {returnRecord.status === "REQUESTED" && (
                <button
                  className={styles.button}
                  type="button"
                  onClick={() => void handleProcessReturn(returnRecord.return_id)}
                  disabled={isReturnOperationActive}
                >
                  {processingReturnId === returnRecord.return_id
                    ? "Processing return..."
                    : "Resume return processing"}
                </button>
              )}
            </article>
          ))}
        </div>
      )}

      {recoveryReturnId && !hasCurrentRecoveryReturn && (
        <div className={styles.returnRecovery}>
          <p>
            A return request was created, but its current status is not
            available yet.
          </p>
          <button
            className={styles.button}
            type="button"
            onClick={() => void handleProcessReturn(recoveryReturnId)}
            disabled={isReturnOperationActive || isLoadingReturns}
          >
            {processingReturnId === recoveryReturnId
              ? "Processing return..."
              : "Retry return processing"}
          </button>
        </div>
      )}

      {!isLoadingReturns &&
        !returnsLoadError &&
        !hasRefundablePayment && (
          <p>Only orders with a remaining paid balance can be returned.</p>
        )}

      {!isLoadingReturns &&
        !returnsLoadError &&
        hasRefundablePayment &&
        !mayStartReturn && (
          <p>
            A return can be started after the order is delivered or collected.
          </p>
        )}

      {mayStartReturn && !isLoadingReturns && !returnsLoadError && (
        <form className={styles.returnForm} onSubmit={handleSubmitReturn}>
          <fieldset disabled={isReturnOperationActive}>
            <legend>Select items to return</legend>
            {selectableUnits.length === 0 ? (
              <p>All items in this order already have return requests.</p>
            ) : (
              <div className={styles.returnChoices}>
                {selectableUnits.map(({ line, unit, unitNumber }) => (
                  <label
                    className={styles.returnChoice}
                    key={unit.order_line_unit_id}
                  >
                    <input
                      type="checkbox"
                      checked={selectedUnitIds.includes(
                        unit.order_line_unit_id,
                      )}
                      onChange={(event) =>
                        toggleSelectedUnit(
                          unit.order_line_unit_id,
                          event.target.checked,
                        )
                      }
                    />
                    <img
                      className={styles.returnItemImage}
                      src={line.image_url}
                      alt=""
                    />
                    <span>
                      {line.product_name} · item {unitNumber} ·{" "}
                      {formatVariantDescription(line.variant)}
                    </span>
                  </label>
                ))}
              </div>
            )}
            <label className={styles.returnReason}>
              <span>Reason for return (required)</span>
              <select
                name="reasonCategory"
                required
                value={reasonCategory}
                aria-describedby="return-reason-help"
                onChange={(event) => {
                  const selectedReason = event.target.value;
                  setReasonCategory(
                    isCustomerReturnReason(selectedReason)
                      ? selectedReason
                      : "",
                  );
                }}
              >
                <option value="">Choose a reason</option>
                <option value="DAMAGED">Damaged</option>
                <option value="WRONG_SIZE">Wrong size</option>
                <option value="WRONG_COLOUR">Wrong colour</option>
                <option value="OTHER">Other</option>
              </select>
              <span
                className={styles.returnReasonHelp}
                id="return-reason-help"
              >
                Add details if you choose Other.
              </span>
            </label>
            <label className={styles.returnReason}>
              <span>
                Additional details (
                {reasonCategory === "OTHER" ? "required" : "optional"})
              </span>
              <textarea
                name="reasonDetails"
                maxLength={MAX_RETURN_REASON_DETAILS_LENGTH}
                rows={4}
                required={reasonCategory === "OTHER"}
                value={reasonDetails}
                onChange={(event) =>
                  setReasonDetails(
                    event.target.value.slice(
                      0,
                      MAX_RETURN_REASON_DETAILS_LENGTH,
                    ),
                  )
                }
              />
            </label>
          </fieldset>
          <button
            className={styles.button}
            type="submit"
            disabled={
              isReturnOperationActive ||
              selectableUnits.length === 0 ||
              selectedUnitIds.length === 0 ||
              !reasonCategory ||
              (reasonCategory === "OTHER" && !reasonDetails.trim())
            }
          >
            {isSubmitting ? "Submitting return..." : "Submit return"}
          </button>
        </form>
      )}
    </section>
  );
}

export function CustomerOrderDetailPage() {
  const { orderId } = useParams();
  const [retryCount, setRetryCount] = useState(0);
  const [loadResult, setLoadResult] =
    useState<CustomerOrderLoadResult | null>(null);

  useEffect(() => {
    if (orderId === undefined) return;

    const requestedOrderId = orderId;

    let isCurrent = true;

    async function loadOrder() {
      try {
        const order =
          await ordersApi.getCustomerOrderDetails(requestedOrderId);

        if (order.channel !== "ECOM") {
          throw new Error("Order not found.");
        }

        let fulfillment: CustomerOrderShipmentStatus | null = null;
        let fulfillmentError: string | null = null;
        const shipmentStatuses = new Set([
          "PAID",
          "READY_TO_SHIP",
          "READY_FOR_PICKUP",
          "SHIPPED",
          "DELIVERED",
          "PICKED_UP",
        ]);
        const mayHaveShipment =
          shipmentStatuses.has(order.order_status) ||
          (order.order_status === "CANCELLED" &&
            order.payment_status === "REFUNDED");

        if (mayHaveShipment) {
          try {
            fulfillment =
              await ordersApi.getCustomerOrderStatus(requestedOrderId);
          } catch (statusError: unknown) {
            fulfillmentError = getOrderErrorMessage(
              statusError,
              "Shipment progress is temporarily unavailable.",
            );
          }
        }

        if (isCurrent) {
          setLoadResult({
            requestedOrderId,
            requestedRetryCount: retryCount,
            kind: "loaded",
            order,
            fulfillment,
            fulfillmentError,
          });
        }
      } catch (loadError: unknown) {
        if (isCurrent) {
          setLoadResult({
            requestedOrderId,
            requestedRetryCount: retryCount,
            kind: "error",
            message: getOrderErrorMessage(
              loadError,
              "Order details could not be loaded. Please try again.",
            ),
          });
        }
      }
    }

    void loadOrder();

    return () => {
      isCurrent = false;
    };
  }, [orderId, retryCount]);

  if (!orderId) {
    return (
      <section className={styles.section}>
        <p className={styles.error} role="alert">
          This order link is missing an order ID.
        </p>
        <Link to="/account">Back to your orders</Link>
      </section>
    );
  }

  if (
    !loadResult ||
    loadResult.requestedOrderId !== orderId ||
    loadResult.requestedRetryCount !== retryCount
  ) {
    return (
      <section className={styles.section}>
        <p role="status">Loading order details...</p>
      </section>
    );
  }

  if (loadResult.kind === "error") {
    return (
      <section className={styles.section}>
        <p className={styles.error} role="alert">
          {loadResult.message}
        </p>
        <div className={styles.detailActions}>
          <button
            className={styles.textButton}
            type="button"
            onClick={() => setRetryCount((currentCount) => currentCount + 1)}
          >
            Try again
          </button>
          <Link to="/account">Back to your orders</Link>
        </div>
      </section>
    );
  }

  const { order, fulfillment, fulfillmentError } = loadResult;

  return (
    <section className={styles.section} aria-labelledby="order-heading">
      <div className={styles.detailHeader}>
        <div>
          <p className={styles.eyebrow}>Order details</p>
          <h1 className={styles.heading} id="order-heading">
            Order {order.order_number}
          </h1>
        </div>
        <Link to="/account">Back to your orders</Link>
      </div>

      <dl className={styles.summaryGrid}>
        <div>
          <dt>Order status</dt>
          <dd>{formatStatus(order.order_status)}</dd>
        </div>
        <div>
          <dt>Placed</dt>
          <dd>
            <time dateTime={order.created_at}>
              {formatOrderDate(order.created_at)}
            </time>
          </dd>
        </div>
        <div>
          <dt>Total</dt>
          <dd>{formatOrderMoney(order.total_gross, order.currency)}</dd>
        </div>
      </dl>

      {fulfillmentError && (
        <p className={styles.error} role="alert">
          {fulfillmentError}
        </p>
      )}

      {fulfillment && (
        <section
          className={styles.fulfillment}
          aria-labelledby="fulfillment-heading"
        >
          <h2 id="fulfillment-heading">Fulfillment progress</h2>
          <dl className={styles.timeline}>
            <div>
              <dt>Shipment status</dt>
              <dd>{formatStatus(fulfillment.status)}</dd>
            </div>
            {fulfillment.mock_tracking_number && (
              <div>
                <dt>Tracking reference</dt>
                <dd>{fulfillment.mock_tracking_number}</dd>
              </div>
            )}
            <StatusDate label="Shipped" value={fulfillment.shipped_at} />
            <StatusDate label="Delivered" value={fulfillment.delivered_at} />
            <StatusDate
              label="Ready for pickup"
              value={fulfillment.ready_for_pickup_at}
            />
            <StatusDate
              label="Pickup deadline"
              value={fulfillment.pickup_deadline_at}
            />
            <StatusDate label="Collected" value={fulfillment.picked_up_at} />
          </dl>
        </section>
      )}

      <CustomerReturnPanel
        key={order.order_id}
        order={order}
        fulfillment={fulfillment}
      />

      <section className={styles.lines} aria-labelledby="items-heading">
        <h2 id="items-heading">Items</h2>
        <ul className={styles.lineList}>
          {order.lines.map((line) => (
            <li className={styles.lineItem} key={line.order_line_id}>
              <div className={styles.orderLineProduct}>
                <img
                  className={styles.orderItemImage}
                  src={line.image_url}
                  alt=""
                  loading="lazy"
                />
                <div>
                  <h3>{line.product_name}</h3>
                  <p>{formatVariantDescription(line.variant)}</p>
                  <p>SKU: {displayOrderSku(line.sku, line.variant)}</p>
                </div>
              </div>
              <dl className={styles.lineAmounts}>
                <div>
                  <dt>Quantity</dt>
                  <dd>{line.quantity}</dd>
                </div>
                <div>
                  <dt>Unit price</dt>
                  <dd>
                    {formatOrderMoney(line.unit_price_gross, order.currency)}
                  </dd>
                </div>
                <div>
                  <dt>Line total</dt>
                  <dd>{formatOrderMoney(line.total_gross, order.currency)}</dd>
                </div>
              </dl>
            </li>
          ))}
        </ul>
      </section>
    </section>
  );
}
