import axios from "axios";
import { useRef, useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import {
  posApi,
  type POSReturnRequest,
} from "../../api/pos";
import type { CustomerOrderDetails } from "../../api/orders";
import {
  formatVariantDescription,
  formatEuroPrice,
} from "../../catalogDisplay";
import { formatApiErrorDetail } from "../../api/errors";
import { useAuth } from "../auth/AuthProvider";
import styles from "./PosOrdersPage.module.css";

function getErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    return formatApiErrorDetail(error.response?.data?.detail, fallback);
  }

  if (error instanceof Error) return error.message;
  return fallback;
}

function formatOrderAmount(amount: string, currency: string): string {
  return currency === "EUR" ? formatEuroPrice(amount) : `${amount} ${currency}`;
}

export function PosOrdersPage() {
  const { session, logout } = useAuth();
  const [orderNumber, setOrderNumber] = useState("");
  const [order, setOrder] = useState<CustomerOrderDetails | null>(null);
  const [selectedUnitIds, setSelectedUnitIds] = useState<Set<string>>(
    new Set(),
  );
  const [returnReason, setReturnReason] = useState("");
  const [isLookingUp, setIsLookingUp] = useState(false);
  const [isSubmittingReturn, setIsSubmittingReturn] = useState(false);
  const [lookupError, setLookupError] = useState<string | null>(null);
  const [returnError, setReturnError] = useState<string | null>(null);
  const [returnMessage, setReturnMessage] = useState<string | null>(null);
  const [managerOrderNumber, setManagerOrderNumber] = useState("");
  const [managerResetConfirmed, setManagerResetConfirmed] = useState(false);
  const [isResettingClaim, setIsResettingClaim] = useState(false);
  const [resetError, setResetError] = useState<string | null>(null);
  const [resetMessage, setResetMessage] = useState<string | null>(null);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState<string | null>(null);
  const returnInProgressRef = useRef(false);
  const claimResetInProgressRef = useRef(false);

  async function loadOrderDetails(
    requestedOrderNumber: string,
  ): Promise<CustomerOrderDetails> {
    return posApi.getStaffOrderDetails(requestedOrderNumber.trim());
  }

  async function handleLookup(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (isLookingUp || !orderNumber.trim()) return;

    setIsLookingUp(true);
    setLookupError(null);
    setReturnError(null);
    setReturnMessage(null);
    setSelectedUnitIds(new Set());

    try {
      const details = await loadOrderDetails(orderNumber);
      setOrder(details);
    } catch (error: unknown) {
      setOrder(null);
      setLookupError(
        getErrorMessage(
          error,
          "The POS order could not be found or loaded.",
        ),
      );
    } finally {
      setIsLookingUp(false);
    }
  }

  function toggleUnit(unitId: string): void {
    setSelectedUnitIds((currentSelection) => {
      const nextSelection = new Set(currentSelection);
      if (nextSelection.has(unitId)) {
        nextSelection.delete(unitId);
      } else {
        nextSelection.add(unitId);
      }
      return nextSelection;
    });
  }

  async function handleReturn(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (
      !order ||
      selectedUnitIds.size === 0 ||
      !returnReason.trim() ||
      returnInProgressRef.current
    ) {
      return;
    }

    returnInProgressRef.current = true;
    setIsSubmittingReturn(true);
    setReturnError(null);
    setReturnMessage(null);

    const request: POSReturnRequest = {
      requested_outcome: "REFUND",
      reason: returnReason.trim(),
      items: Array.from(selectedUnitIds, (orderLineUnitId) => ({
        order_line_unit_id: orderLineUnitId,
      })),
    };

    try {
      const result = await posApi.processStaffReturn(order.order_id, request);
      setReturnMessage(
        `Return completed for ${result.returned_item_count} item(s). A simulated ${formatOrderAmount(result.amount, order.currency)} refund was recorded.`,
      );
      setSelectedUnitIds(new Set());
      setReturnReason("");

      try {
        setOrder(await loadOrderDetails(order.order_number));
      } catch (error: unknown) {
        setLookupError(
          `The return completed, but the order details could not be refreshed: ${getErrorMessage(error, "Please look up the order again.")}`,
        );
        setOrder(null);
      }
    } catch (error: unknown) {
      setReturnError(
        getErrorMessage(error, "The POS return could not be completed."),
      );
    } finally {
      returnInProgressRef.current = false;
      setIsSubmittingReturn(false);
    }
  }

  async function handleClaimReset(
    event: FormEvent<HTMLFormElement>,
  ): Promise<void> {
    event.preventDefault();
    if (
      !managerResetConfirmed ||
      !managerOrderNumber.trim() ||
      claimResetInProgressRef.current
    ) {
      return;
    }

    claimResetInProgressRef.current = true;
    setIsResettingClaim(true);
    setResetError(null);
    setResetMessage(null);

    try {
      await posApi.resetClaimByOrderNumber(managerOrderNumber.trim());
      setResetMessage(
        `Claim attempts were reset for ${managerOrderNumber.trim()}. The customer can try the claim code again.`,
      );
      setManagerOrderNumber("");
      setManagerResetConfirmed(false);
    } catch (error: unknown) {
      setResetError(
        getErrorMessage(
          error,
          "The claim lock could not be reset. Confirm that this POS order is currently locked.",
        ),
      );
    } finally {
      claimResetInProgressRef.current = false;
      setIsResettingClaim(false);
    }
  }

  async function handleLogout(): Promise<void> {
    if (isLoggingOut) return;

    setIsLoggingOut(true);
    setLogoutError(null);
    try {
      await logout();
    } catch (error: unknown) {
      setLogoutError(
        getErrorMessage(error, "Sign out failed. Please try again."),
      );
    } finally {
      setIsLoggingOut(false);
    }
  }

  if (session?.kind !== "staff") return null;

  const isManager = session.profile.role === "STORE_MANAGER";
  const canSubmitReturn =
    selectedUnitIds.size > 0 &&
    returnReason.trim().length > 0 &&
    !isSubmittingReturn;

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <div>
          <p className={styles.eyebrow}>Half Moon · Staff operations</p>
          <h1 className={styles.heading}>POS orders</h1>
          <p className={styles.identity}>
            Signed in as {session.profile.email} ({session.profile.role})
          </p>
        </div>
        <nav className={styles.actions} aria-label="Staff navigation">
          <Link className={styles.button} to="/pos">
            Back to checkout
          </Link>
          <button
            className={styles.button}
            type="button"
            onClick={() => void handleLogout()}
            disabled={isLoggingOut}
          >
            {isLoggingOut ? "Signing out..." : "Sign out"}
          </button>
        </nav>
      </header>

      {logoutError && (
        <p className={styles.error} role="alert">
          {logoutError}
        </p>
      )}

      <div className={styles.content}>
        <section className={styles.panel} aria-labelledby="lookup-heading">
          <p className={styles.eyebrow}>Claimed customer sales</p>
          <h2 id="lookup-heading" className={styles.sectionHeading}>
            Find an order
          </h2>
          <p className={styles.description}>
            Enter the order number from the customer receipt. Unclaimed orders
            are not available through staff lookup.
          </p>
          <form className={styles.form} onSubmit={(event) => void handleLookup(event)}>
            <label className={styles.field}>
              <span>POS order number</span>
              <input
                autoComplete="off"
                maxLength={30}
                required
                value={orderNumber}
                onChange={(event) => setOrderNumber(event.target.value)}
              />
            </label>
            <button
              className={styles.primaryButton}
              type="submit"
              disabled={isLookingUp || !orderNumber.trim()}
            >
              {isLookingUp ? "Looking up..." : "Look up order"}
            </button>
          </form>

          {lookupError && (
            <p className={styles.error} role="alert">
              {lookupError}
            </p>
          )}
          {returnMessage && (
            <p className={styles.success} role="status">
              {returnMessage}
            </p>
          )}

          {order && (
            <div className={styles.orderDetails}>
              <dl className={styles.summary}>
                <div>
                  <dt>Order</dt>
                  <dd>{order.order_number}</dd>
                </div>
                <div>
                  <dt>Status</dt>
                  <dd>{order.order_status.replaceAll("_", " ")}</dd>
                </div>
                <div>
                  <dt>Payment</dt>
                  <dd>{order.payment_status.replaceAll("_", " ")}</dd>
                </div>
                <div>
                  <dt>Total</dt>
                  <dd>{formatOrderAmount(order.total_gross, order.currency)}</dd>
                </div>
              </dl>

              <form className={styles.returnForm} onSubmit={(event) => void handleReturn(event)}>
                <h3 className={styles.subheading}>Process a return</h3>
                <p className={styles.description}>
                  Select only the specific unreturned items the customer is
                  returning. The backend validates the return window and
                  refund.
                </p>
                <ul className={styles.lineList}>
                  {order.lines.flatMap((line) =>
                    line.units.map((unit, unitIndex) => (
                      <li key={unit.order_line_unit_id}>
                        <label className={styles.unitChoice}>
                          <input
                            type="checkbox"
                            checked={selectedUnitIds.has(
                              unit.order_line_unit_id,
                            )}
                            disabled={
                              unit.already_returned || isSubmittingReturn
                            }
                            onChange={() =>
                              toggleUnit(unit.order_line_unit_id)
                            }
                          />
                          <span>
                            <strong>{line.product_name}</strong>
                            <span>
                              {formatVariantDescription(line.variant, " · ")} · Item{" "}
                              {unitIndex + 1} of {line.quantity}
                            </span>
                            <span>
                              {unit.already_returned
                                ? "Already returned"
                                : formatOrderAmount(
                                    line.unit_price_gross,
                                    order.currency,
                                  )}
                            </span>
                          </span>
                        </label>
                      </li>
                    )),
                  )}
                </ul>
                <label className={styles.field}>
                  <span>Return reason</span>
                  <textarea
                    maxLength={500}
                    required
                    rows={3}
                    value={returnReason}
                    onChange={(event) => setReturnReason(event.target.value)}
                  />
                </label>
                {returnError && (
                  <p className={styles.error} role="alert">
                    {returnError}
                  </p>
                )}
                <button
                  className={styles.primaryButton}
                  type="submit"
                  disabled={!canSubmitReturn}
                >
                  {isSubmittingReturn ? "Processing return..." : "Process refund"}
                </button>
              </form>
            </div>
          )}
        </section>

        {isManager && (
          <section
            className={styles.panel}
            aria-labelledby="claim-reset-heading"
          >
            <p className={styles.eyebrow}>Manager only</p>
            <h2 id="claim-reset-heading" className={styles.sectionHeading}>
              Reset a locked claim
            </h2>
            <p className={styles.description}>
              This resets claim attempts for a locked POS order without
              exposing its details. It does not reset an order that is already
              claimed.
            </p>
            <form
              className={styles.form}
              onSubmit={(event) => void handleClaimReset(event)}
            >
              <label className={styles.field}>
                <span>Locked POS order number</span>
                <input
                  autoComplete="off"
                  maxLength={30}
                  required
                  value={managerOrderNumber}
                  onChange={(event) => setManagerOrderNumber(event.target.value)}
                />
              </label>
              <label className={styles.confirmation}>
                <input
                  type="checkbox"
                  checked={managerResetConfirmed}
                  onChange={(event) =>
                    setManagerResetConfirmed(event.target.checked)
                  }
                />
                <span>I verified this order number with the customer receipt.</span>
              </label>
              {resetError && (
                <p className={styles.error} role="alert">
                  {resetError}
                </p>
              )}
              {resetMessage && (
                <p className={styles.success} role="status">
                  {resetMessage}
                </p>
              )}
              <button
                className={styles.primaryButton}
                type="submit"
                disabled={
                  isResettingClaim ||
                  !managerOrderNumber.trim() ||
                  !managerResetConfirmed
                }
              >
                {isResettingClaim ? "Resetting..." : "Reset claim attempts"}
              </button>
            </form>
          </section>
        )}
      </div>
    </main>
  );
}
