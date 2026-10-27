const REQUEST_LOCATION_PREFIXES = new Set([
  "body",
  "query",
  "path",
  "header",
  "cookie",
]);

function formatIssueLocation(location: unknown): string | null {
  if (!Array.isArray(location)) return null;

  const pathSegments = location
    .filter(
      (segment): segment is string | number =>
        typeof segment === "string" || typeof segment === "number",
    )
    .filter(
      (segment) =>
        typeof segment !== "string" ||
        !REQUEST_LOCATION_PREFIXES.has(segment),
    );

  return pathSegments.length > 0 ? pathSegments.join(".") : null;
}

function formatValidationIssue(issue: unknown): string | null {
  if (typeof issue !== "object" || issue === null || !("msg" in issue)) {
    return null;
  }

  const message = issue.msg;
  if (typeof message !== "string" || message.trim().length === 0) {
    return null;
  }

  const location = "loc" in issue ? formatIssueLocation(issue.loc) : null;
  const trimmedMessage = message.trim();

  return location ? `${location}: ${trimmedMessage}` : trimmedMessage;
}

export function formatApiErrorDetail(
  detail: unknown,
  fallback: string,
): string {
  if (typeof detail === "string") {
    const trimmedDetail = detail.trim();
    return trimmedDetail.length > 0 ? trimmedDetail : fallback;
  }

  if (!Array.isArray(detail)) return fallback;

  const issueMessages = detail
    .map(formatValidationIssue)
    .filter((message): message is string => message !== null);

  return issueMessages.length > 0 ? issueMessages.join("; ") : fallback;
}
