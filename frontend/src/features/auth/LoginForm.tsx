import axios from "axios";
import { useRef, useState, type FormEvent } from "react";
import {
  Link,
  Navigate,
  useLocation,
  useNavigate,
  useSearchParams,
} from "react-router-dom";
import { authApi } from "../../api/auth";
import { formatApiErrorDetail } from "../../api/errors";
import { useAuth } from "./AuthProvider";
import styles from "./Auth.module.css";

type LoginKind = "customer" | "staff";

function getSafeCustomerReturnPath(locationState: unknown): string | null {
  if (typeof locationState !== "object" || locationState === null) return null;

  if (!("returnTo" in locationState)) return null;

  const returnTo = locationState.returnTo;
  if (typeof returnTo !== "string") return null;

  const claimOrderPath = "/account/claim-pos-order";
  if (returnTo.startsWith(`${claimOrderPath}?`)) {
    const returnUrl = new URL(returnTo, window.location.origin);
    const orderNumbers = returnUrl.searchParams.getAll("order_number");
    const orderNumber = orderNumbers[0];

    if (
      returnUrl.origin !== window.location.origin ||
      returnUrl.pathname !== claimOrderPath ||
      returnUrl.searchParams.size !== 1 ||
      orderNumbers.length !== 1 ||
      !orderNumber ||
      orderNumber.length > 30 ||
      !/^[A-Za-z0-9-]+$/.test(orderNumber)
    ) {
      return null;
    }

    return `${claimOrderPath}?order_number=${encodeURIComponent(orderNumber)}`;
  }

  if (
    returnTo === "/checkout" ||
    returnTo === "/account" ||
    returnTo === claimOrderPath ||
    returnTo === "/account/pos-purchases" ||
    returnTo.startsWith("/account/pos-purchases/") ||
    returnTo.startsWith("/account/orders/")
  ) {
    return returnTo;
  }

  return null;
}

function getErrorMessage(
  error: unknown,
  fallback = "Sign in failed. Please try again.",
): string {
  if (axios.isAxiosError<{ detail?: unknown }>(error)) {
    return formatApiErrorDetail(error.response?.data?.detail, fallback);
  }

  if (error instanceof Error) return error.message;

  return fallback;
}

function SignInForm({ kind }: { kind: LoginKind }) {
  const {
    session,
    status,
    sessionError,
    loginCustomer,
    loginStaff,
    retrySessionRestore,
  } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const customerReturnPath = getSafeCustomerReturnPath(location.state);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const isStaff = kind === "staff";
  const isBusy = isSubmitting || status === "loading";

  if (session?.kind === "staff" && isStaff) {
    return <Navigate to="/pos" replace />;
  }

  if (session?.kind === "customer" && !isStaff) {
    return <Navigate to={customerReturnPath ?? "/"} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (isBusy) return;

    setIsSubmitting(true);
    setFormError(null);

    try {
      if (isStaff) {
        await loginStaff(email, password);
        navigate("/pos", { replace: true });
      } else {
        await loginCustomer(email, password);
        navigate(customerReturnPath ?? "/", { replace: true });
      }
    } catch (error: unknown) {
      setFormError(getErrorMessage(error));
    } finally {
      setIsSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.panel} aria-labelledby="login-heading">
        <p className={styles.eyebrow}>
          {isStaff ? "Staff access" : "Customer account"}
        </p>
        <h1 className={styles.heading} id="login-heading">
          {isStaff ? "Staff sign in" : "Sign in"}
        </h1>

        {status === "error" && sessionError && (
          <div className={styles.error} role="alert">
            <p>{sessionError}</p>
            <button
              className={styles.textButton}
              type="button"
              onClick={() => void retrySessionRestore()}
            >
              Retry session check
            </button>
          </div>
        )}

        {formError && (
          <p className={styles.error} role="alert">
            {formError}
          </p>
        )}

        <form className={styles.form} onSubmit={handleSubmit}>
          <label className={styles.field}>
            <span>Email</span>
            <input
              autoComplete="username"
              type="email"
              name="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              disabled={isBusy}
            />
          </label>

          <label className={styles.field}>
            <span>Password</span>
            <input
              autoComplete="current-password"
              type="password"
              name="password"
              required
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              disabled={isBusy}
            />
          </label>

          <button className={styles.button} type="submit" disabled={isBusy}>
            {isSubmitting ? "Signing in..." : "Sign in"}
          </button>
        </form>

        <p className={styles.alternate}>
          {isStaff ? "Shopping at Half Moon?" : "Store staff?"}{" "}
          <Link to={isStaff ? "/login" : "/staff/login"}>
            {isStaff ? "Customer sign in" : "Staff sign in"}
          </Link>
        </p>
        {!isStaff && (
          <div className={styles.accountLinks}>
            <Link to="/register">Create an account</Link>
            <Link to="/reset-password">Forgot your password?</Link>
          </div>
        )}
      </section>
    </main>
  );
}

export function CustomerLoginPage() {
  return <SignInForm kind="customer" />;
}

export function StaffLoginPage() {
  return <SignInForm kind="staff" />;
}

export function CustomerRegistrationPage() {
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isSubmittingRef = useRef(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (isSubmittingRef.current) return;

    isSubmittingRef.current = true;
    setIsSubmitting(true);
    setFormError(null);
    setNotice(null);

    try {
      const response = await authApi.registerCustomer({
        email,
        password,
        first_name: firstName,
        last_name: lastName,
      });

      setNotice(
        `${response.message}. Check your email for the verification link before signing in.`,
      );
    } catch (error: unknown) {
      setFormError(
        getErrorMessage(error, "Account registration failed. Please try again."),
      );
    } finally {
      isSubmittingRef.current = false;
      setIsSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.panel} aria-labelledby="register-heading">
        <p className={styles.eyebrow}>Customer account</p>
        <h1 className={styles.heading} id="register-heading">
          Create an account
        </h1>

        {formError && (
          <p className={styles.error} role="alert">
            {formError}
          </p>
        )}
        {notice && (
          <p className={styles.notice} role="status">
            {notice}
          </p>
        )}

        {!notice && (
          <form className={styles.form} onSubmit={handleSubmit}>
            <label className={styles.field}>
              <span>First name</span>
              <input
                autoComplete="given-name"
                type="text"
                name="first_name"
                maxLength={100}
                required
                value={firstName}
                onChange={(event) => setFirstName(event.target.value)}
                disabled={isSubmitting}
              />
            </label>

            <label className={styles.field}>
              <span>Last name</span>
              <input
                autoComplete="family-name"
                type="text"
                name="last_name"
                maxLength={100}
                required
                value={lastName}
                onChange={(event) => setLastName(event.target.value)}
                disabled={isSubmitting}
              />
            </label>

            <label className={styles.field}>
              <span>Email</span>
              <input
                autoComplete="email"
                type="email"
                name="email"
                required
                value={email}
                onChange={(event) => setEmail(event.target.value)}
                disabled={isSubmitting}
              />
            </label>

            <label className={styles.field}>
              <span>Password</span>
              <input
                autoComplete="new-password"
                type="password"
                name="password"
                minLength={8}
                maxLength={128}
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={isSubmitting}
              />
            </label>

            <button
              className={styles.button}
              type="submit"
              disabled={isSubmitting}
            >
              {isSubmitting ? "Creating account..." : "Create account"}
            </button>
          </form>
        )}

        <p className={styles.alternate}>
          <Link to="/login">Already have an account? Sign in</Link>
        </p>
      </section>
    </main>
  );
}

export function EmailVerificationPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token")?.trim() ?? "";
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isSubmittingRef = useRef(false);
  const [isVerified, setIsVerified] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (isSubmittingRef.current || !token) return;

    isSubmittingRef.current = true;
    setIsSubmitting(true);
    setFormError(null);

    try {
      const response = await authApi.verifyCustomerEmail(token);
      setNotice(response.message);
      setIsVerified(true);
      window.history.replaceState(
        window.history.state,
        "",
        window.location.pathname,
      );
    } catch (error: unknown) {
      setFormError(
        getErrorMessage(
          error,
          "Email verification failed. The link may be expired or invalid.",
        ),
      );
    } finally {
      isSubmittingRef.current = false;
      setIsSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.panel} aria-labelledby="verification-heading">
        <p className={styles.eyebrow}>Customer account</p>
        <h1 className={styles.heading} id="verification-heading">
          Verify your email
        </h1>

        {formError && (
          <p className={styles.error} role="alert">
            {formError}
          </p>
        )}

        {isVerified ? (
          <>
            <p className={styles.notice} role="status">
              {notice}
            </p>
            <p className={styles.alternate}>
              <Link to="/login">Continue to sign in</Link>
            </p>
          </>
        ) : token ? (
          <form className={styles.form} onSubmit={handleSubmit}>
            <p>
              Confirm your email address to activate your customer account.
            </p>
            <button
              className={styles.button}
              type="submit"
              disabled={isSubmitting}
            >
              {isSubmitting ? "Verifying..." : "Verify email"}
            </button>
          </form>
        ) : (
          <p className={styles.error} role="alert">
            This verification link does not contain a token. Open the link from
            your verification email or contact support.
          </p>
        )}
      </section>
    </main>
  );
}

export function PasswordResetPage() {
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token")?.trim() ?? "";
  const isConfirming = token.length > 0;
  const [email, setEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const isSubmittingRef = useRef(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();

    if (isSubmittingRef.current) return;

    isSubmittingRef.current = true;
    setIsSubmitting(true);
    setFormError(null);
    setNotice(null);

    try {
      if (token) {
        const response = await authApi.confirmPasswordReset(token, newPassword);
        setNotice(response.message);
        window.history.replaceState(
          window.history.state,
          "",
          window.location.pathname,
        );
      } else {
        const response = await authApi.requestPasswordReset(email);
        setNotice(response.message);
      }
    } catch (error: unknown) {
      setFormError(
        getErrorMessage(error, "Password reset failed. Please try again."),
      );
    } finally {
      isSubmittingRef.current = false;
      setIsSubmitting(false);
    }
  }

  return (
    <main className={styles.page}>
      <section className={styles.panel} aria-labelledby="reset-heading">
        <p className={styles.eyebrow}>Customer account</p>
        <h1 className={styles.heading} id="reset-heading">
          {isConfirming ? "Choose a new password" : "Reset your password"}
        </h1>

        {formError && (
          <p className={styles.error} role="alert">
            {formError}
          </p>
        )}

        {notice ? (
          <>
            <p className={styles.notice} role="status">
              {notice}
            </p>
            <p className={styles.alternate}>
              <Link to="/login">Return to sign in</Link>
            </p>
          </>
        ) : (
          <form className={styles.form} onSubmit={handleSubmit}>
            {isConfirming ? (
              <label className={styles.field}>
                <span>New password</span>
                <input
                  autoComplete="new-password"
                  type="password"
                  name="new_password"
                  minLength={8}
                  maxLength={128}
                  required
                  value={newPassword}
                  onChange={(event) => setNewPassword(event.target.value)}
                  disabled={isSubmitting}
                />
              </label>
            ) : (
              <label className={styles.field}>
                <span>Email</span>
                <input
                  autoComplete="email"
                  type="email"
                  name="email"
                  required
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                  disabled={isSubmitting}
                />
              </label>
            )}

            <button
              className={styles.button}
              type="submit"
              disabled={isSubmitting}
            >
              {isSubmitting
                ? "Please wait..."
                : isConfirming
                  ? "Save new password"
                  : "Send reset link"}
            </button>
          </form>
        )}

        {!notice && (
          <p className={styles.alternate}>
            <Link to="/login">Return to sign in</Link>
          </p>
        )}
      </section>
    </main>
  );
}
