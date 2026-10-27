import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  clearAccessToken,
  getAccessRole,
  hasCsrfTokenCookie,
  refreshAccessToken,
  setAccessToken,
  subscribeToSessionInvalidation,
} from "../../api/client";
import {
  authApi,
  type CustomerProfile,
  type StaffProfile,
} from "../../api/auth";

export type StaffRole = "SALES_ASSOCIATE" | "STORE_MANAGER";

export type AuthSession =
  | { kind: "customer"; profile: CustomerProfile }
  | { kind: "staff"; profile: StaffProfile & { role: StaffRole } };

export type AuthStatus = "loading" | "ready" | "error";

interface AuthContextValue {
  session: AuthSession | null;
  status: AuthStatus;
  sessionError: string | null;
  loginCustomer: (email: string, password: string) => Promise<void>;
  loginStaff: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  retrySessionRestore: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function isStaffRole(role: string): role is StaffRole {
  return role === "SALES_ASSOCIATE" || role === "STORE_MANAGER";
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [status, setStatus] = useState<AuthStatus>(() =>
    hasCsrfTokenCookie() ? "loading" : "ready",
  );
  const [sessionError, setSessionError] = useState<string | null>(null);
  const invalidatedDuringRestore = useRef(false);

  const restoreSession = useCallback(async (): Promise<void> => {
    if (!hasCsrfTokenCookie()) return;
    invalidatedDuringRestore.current = false;

    try {
      await refreshAccessToken();

      const role = getAccessRole();

      if (role !== null) {
        const profile = await authApi.getStaffProfile();

        if (!isStaffRole(profile.role) || profile.role !== role) {
          throw new Error("Staff role could not be verified. Sign in again.");
        }

        setSession({
          kind: "staff",
          profile: { ...profile, role: profile.role },
        });
      } else {
        const profile = await authApi.getCustomerProfile();
        setSession({ kind: "customer", profile });
      }

      setSessionError(null);
      setStatus("ready");
    } catch {
      if (invalidatedDuringRestore.current) return;

      clearAccessToken();
      setSession(null);
      setSessionError(
        "Your session could not be restored. Check your connection and try again.",
      );
      setStatus("error");
    }
  }, []);

  const retrySessionRestore = useCallback(async (): Promise<void> => {
    if (!hasCsrfTokenCookie()) {
      clearAccessToken();
      setSession(null);
      setSessionError(null);
      setStatus("ready");
      return;
    }

    setSessionError(null);
    setStatus("loading");
    await restoreSession();
  }, [restoreSession]);

  useEffect(() => {
    const unsubscribe = subscribeToSessionInvalidation(() => {
      invalidatedDuringRestore.current = true;
      setSession(null);
      setSessionError(null);
      setStatus("ready");
    });

    // Session restoration synchronizes this provider with the refresh-cookie session.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void restoreSession();

    return unsubscribe;
  }, [restoreSession]);

  const loginCustomer = useCallback(
    async (email: string, password: string): Promise<void> => {
      setSessionError(null);
      setStatus("loading");

      try {
        const token = await authApi.loginCustomer(email, password);
        setAccessToken(token.access_token);
        const profile = await authApi.getCustomerProfile();

        setSession({ kind: "customer", profile });
        setStatus("ready");
      } catch (error: unknown) {
        clearAccessToken();
        setSession(null);
        setStatus("ready");
        throw error;
      }
    },
    [],
  );

  const loginStaff = useCallback(
    async (email: string, password: string): Promise<void> => {
      setSessionError(null);
      setStatus("loading");

      try {
        const token = await authApi.loginStaff(email, password);

        if (!isStaffRole(token.role)) {
          throw new Error("This staff account does not have a recognized role.");
        }

        setAccessToken(token.access_token, token.role);
        const profile = await authApi.getStaffProfile();

        if (!isStaffRole(profile.role) || profile.role !== token.role) {
          throw new Error("Staff role could not be verified. Sign in again.");
        }

        setSession({
          kind: "staff",
          profile: { ...profile, role: profile.role },
        });
        setStatus("ready");
      } catch (error: unknown) {
        clearAccessToken();
        setSession(null);
        setStatus("ready");
        throw error;
      }
    },
    [],
  );

  const logout = useCallback(async (): Promise<void> => {
    await authApi.logout();
    clearAccessToken();
    setSession(null);
    setSessionError(null);
    setStatus("ready");
    window.location.replace("/");
  }, []);

  return (
    <AuthContext.Provider
      value={{
        session,
        status,
        sessionError,
        loginCustomer,
        loginStaff,
        logout,
        retrySessionRestore,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const auth = useContext(AuthContext);

  if (auth === null) {
    throw new Error("useAuth must be used within AuthProvider");
  }

  return auth;
}
