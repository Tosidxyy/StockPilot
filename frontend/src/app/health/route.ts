// Same-origin clients need the health check forwarded just like /api requests.
export async function GET() {
  try {
    const response = await fetch("http://127.0.0.1:8000/health", {
      cache: "no-store",
      signal: AbortSignal.timeout(5000),
    });
    const result: { status?: string } = await response.json();
    if (response.ok && result.status === "ok") {
      return Response.json({ status: "ok" });
    }
  } catch {
    // A failed probe must not be reported as a healthy backend.
  }
  return Response.json({ status: "unavailable" }, { status: 503 });
}
