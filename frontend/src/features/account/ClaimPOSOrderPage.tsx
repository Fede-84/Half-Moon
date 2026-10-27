import axios from "axios";
import { useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { formatApiErrorDetail } from "../../api/errors";
import { posApi } from "../../api/pos";
import styles from "./ClaimPOSOrderPage.module.css";

const CLAIM_CODE_PATTERN = /^HM-\d{8}$/;

function getClaimErrorMessage(error: unknown, fallback: string): string {
  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    return formatApiErrorDetail(error.response?.data?.detail, fallback);
  }

  if (error instanceof Error) return error.message;
  return fallback;
}

export function ClaimPOSOrderPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [orderNumber, setOrderNumber] = useState(
    () => searchParams.get("order_number") ?? "",
  );
  const [claimCode, setClaimCode] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [claimedOrderNumber, setClaimedOrderNumber] = useState<string | null>(
    null,
  );
  const submissionInProgressRef = useRef(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();

    const normalizedOrderNumber = orderNumber.trim();
    const normalizedClaimCode = claimCode.trim().toUpperCase();
    if (
      !normalizedOrderNumber ||
      !CLAIM_CODE_PATTERN.test(normalizedClaimCode) ||
      submissionInProgressRef.current
    ) {
      return;
    }

    submissionInProgressRef.current = true;
    setIsSubmitting(true);
    setError(null);
    setClaimedOrderNumber(null);

    try {
      const response = await posApi.claimCustomerOrder({
        order_number: normalizedOrderNumber,
        claim_code: normalizedClaimCode,
      });
      setClaimedOrderNumber(response.order_number);
      setOrderNumber("");
      setClaimCode("");
      if (searchParams.has("order_number")) {
        setSearchParams({}, { replace: true });
      }
    } catch (claimError: unknown) {
      setError(
        getClaimErrorMessage(
          claimError,
          "The POS order could not be claimed. Please try again.",
        ),
      );
    } finally {
      submissionInProgressRef.current = false;
      setIsSubmitting(false);
    }
  }

  return (
    <section className={styles.section} aria-labelledby="claim-pos-heading">
      <div className={styles.sectionHeader}>
        <div>
          <p className={styles.eyebrow}>Customer account</p>
          <h1 className={styles.heading} id="claim-pos-heading">
            Claim a POS order
          </h1>
        </div>
        <Link to="/account">Back to your account</Link>
      </div>

      <p className={styles.description}>
        Enter the order number and one-time claim code from your store receipt
        to link the purchase to this verified customer account.
      </p>

      <form
        className={styles.form}
        onSubmit={(event) => void handleSubmit(event)}
      >
        <label className={styles.field}>
          <span>POS order number</span>
          <input
            autoComplete="off"
            maxLength={30}
            required
            value={orderNumber}
            onChange={(event) => {
              setOrderNumber(event.target.value);
              setError(null);
              setClaimedOrderNumber(null);
            }}
          />
        </label>

        <label className={styles.field}>
          <span>One-time claim code</span>
          <input
            aria-describedby="claim-code-help"
            autoCapitalize="characters"
            autoComplete="one-time-code"
            maxLength={11}
            pattern="HM-[0-9]{8}"
            required
            spellCheck={false}
            value={claimCode}
            onChange={(event) => {
              setClaimCode(event.target.value.toUpperCase());
              setError(null);
              setClaimedOrderNumber(null);
            }}
          />
          <span className={styles.help} id="claim-code-help">
            Enter the code in the format HM-12345678.
          </span>
        </label>

        {error && (
          <p className={styles.error} role="alert">
            {error}
          </p>
        )}

        {claimedOrderNumber && (
          <div className={styles.success} role="status">
            <p>
              Order {claimedOrderNumber} is now linked to your account.
            </p>
            <p>
              View the purchase in your{" "}
              <Link to="/account/pos-purchases">POS purchases</Link> area to
              track it or request an eligible return.
            </p>
          </div>
        )}

        <button
          className={styles.submitButton}
          type="submit"
          disabled={
            isSubmitting ||
            !orderNumber.trim() ||
            !CLAIM_CODE_PATTERN.test(claimCode.trim().toUpperCase())
          }
        >
          {isSubmitting ? "Claiming order..." : "Claim POS order"}
        </button>
      </form>
    </section>
  );
}
