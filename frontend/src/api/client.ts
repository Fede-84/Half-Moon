import axios from "axios";

declare module "axios" {
  // Axios's generic defaults must match for this declaration to merge safely.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any, @typescript-eslint/no-unused-vars
  interface AxiosRequestConfig<D = any, P = any> {
    _sessionRecoveryAttempted?: boolean;
  }
}

const CSRF_COOKIE_NAME = "half_moon_csrf";
const CSRF_HEADER_NAME = "X-CSRF-Token";
const CUSTOMER_LOGIN_PATH = "/auth/login";
const STAFF_LOGIN_PATH = "/auth/staff/login";
const REFRESH_PATH = "/auth/refresh";

export interface RefreshTokenResponse {
  access_token: string;
  role?: string;
}

let accessToken: string | null = null;
let accessRole: string | null = null;
let refreshInFlight: Promise<string> | null = null;
let redirectInProgress = false;
const sessionInvalidationListeners = new Set<() => void>();

function readCsrfToken(): string | undefined {
  if (typeof document === "undefined") return undefined;

  const cookiePrefix = `${CSRF_COOKIE_NAME}=`;
  const csrfCookie = document.cookie
    .split(";")
    .map((cookie) => cookie.trim())
    .find((cookie) => cookie.startsWith(cookiePrefix));

  if (!csrfCookie) return undefined;

  return csrfCookie.slice(cookiePrefix.length);
}

export function hasCsrfTokenCookie(): boolean {
  return readCsrfToken() !== undefined;
}

function getRequestPath(requestUrl: string | undefined): string | undefined {
  if (!requestUrl) return undefined;

  const requestPath = new URL(requestUrl, "http://half-moon.local").pathname;
  const versionlessPath = requestPath.replace(/^\/api\/v1(?=\/|$)/, "");

  return versionlessPath || "/";
}

export function setAccessToken(token: string, role: string | null = null): void {
  accessToken = token;
  accessRole = role;
  redirectInProgress = false;
}

export function clearAccessToken(): void {
  accessToken = null;
  accessRole = null;
}

export function getAccessRole(): string | null {
  return accessRole;
}

export function subscribeToSessionInvalidation(
  listener: () => void,
): () => void {
  sessionInvalidationListeners.add(listener);

  return () => {
    sessionInvalidationListeners.delete(listener);
  };
}

function getSessionLoginPath(): "/login" | "/staff/login" {
  const currentPath = window.location.pathname;
  const isStaffRoute =
    currentPath === "/pos" ||
    currentPath.startsWith("/pos/") ||
    currentPath.startsWith("/staff/");

  return accessRole !== null || isStaffRoute ? "/staff/login" : "/login";
}

export const apiClient = axios.create({
  baseURL: "/api/v1",
  withCredentials: true,
});

apiClient.interceptors.request.use((config) => {
  const method = config.method?.toUpperCase() ?? "GET";
  const requestPath = getRequestPath(config.url);

  if (accessToken && requestPath !== REFRESH_PATH) {
    config.headers.set("Authorization", `Bearer ${accessToken}`);
  }

  if (method !== "GET") {
    const csrfToken = readCsrfToken();

    if (csrfToken) {
      config.headers.set(CSRF_HEADER_NAME, csrfToken);
    }
  }

  return config;
});

function invalidateSession(): void {
  const loginPath =
    typeof window === "undefined" ? null : getSessionLoginPath();

  clearAccessToken();

  for (const listener of sessionInvalidationListeners) {
    listener();
  }

  if (loginPath === null) return;
  if (window.location.pathname === loginPath) return;
  if (redirectInProgress) return;

  redirectInProgress = true;
  window.location.assign(loginPath);
}

export function refreshAccessToken(): Promise<string> {
  if (refreshInFlight) return refreshInFlight;

  const refreshRequest = apiClient
    .post<RefreshTokenResponse>(REFRESH_PATH)
    .then(({ data }) => {
      setAccessToken(data.access_token, data.role ?? null);
      return data.access_token;
    });

  refreshInFlight = refreshRequest.finally(() => {
    refreshInFlight = null;
  });

  return refreshInFlight;
}

apiClient.interceptors.response.use(
  (response) => response,
  async (error: unknown) => {
    if (!axios.isAxiosError(error)) return Promise.reject(error);

    const responseStatus = error.response?.status;
    const failedRequest = error.config;

    if (!failedRequest) return Promise.reject(error);

    const failedPath = getRequestPath(failedRequest.url);

    if (failedPath === REFRESH_PATH) {
      if (responseStatus === 401 || responseStatus === 403) {
        invalidateSession();
      }

      return Promise.reject(error);
    }

    if (responseStatus !== 401) return Promise.reject(error);

    if (failedPath === CUSTOMER_LOGIN_PATH || failedPath === STAFF_LOGIN_PATH) {
      return Promise.reject(error);
    }

    if (failedRequest._sessionRecoveryAttempted) {
      invalidateSession();
      return Promise.reject(error);
    }

    failedRequest._sessionRecoveryAttempted = true;

    try {
      const refreshedToken = await refreshAccessToken();
      failedRequest.headers.set("Authorization", `Bearer ${refreshedToken}`);

      return await apiClient.request(failedRequest);
    } catch (refreshError: unknown) {
      if (
        axios.isAxiosError(refreshError) &&
        (refreshError.response?.status === 401 ||
          refreshError.response?.status === 403)
      ) {
        invalidateSession();
      }

      return Promise.reject(refreshError);
    }
  },
);
