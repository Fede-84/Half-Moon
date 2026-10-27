import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "./AuthProvider";
import styles from "./Auth.module.css";

export function RequireCustomer({ children }: { children: ReactNode }) {
  const { session, status, sessionError, retrySessionRestore } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <main className={styles.page}>
        <p role="status">Checking customer session...</p>
      </main>
    );
  }

  if (status === "error") {
    return (
      <main className={styles.page}>
        <p className={styles.error} role="alert">
          {sessionError}
        </p>
        <button
          className={styles.button}
          type="button"
          onClick={() => void retrySessionRestore()}
        >
          Retry
        </button>
      </main>
    );
  }

  if (session?.kind !== "customer") {
    const returnTo = `${location.pathname}${location.search}${location.hash}`;

    return <Navigate to="/login" state={{ returnTo }} replace />;
  }

  return children;
}
