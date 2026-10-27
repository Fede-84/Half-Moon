import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useAuth } from "./AuthProvider";
import styles from "./Auth.module.css";

export function RequireStaff({ children }: { children: ReactNode }) {
  const { session, status, sessionError, retrySessionRestore } = useAuth();

  if (status === "loading") {
    return (
      <main className={styles.page}>
        <p role="status">Checking staff session...</p>
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

  if (session?.kind !== "staff") {
    return <Navigate to="/staff/login" replace />;
  }

  return children;
}
