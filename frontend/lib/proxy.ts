import { NextRequest, NextResponse } from "next/server";
export async function proxy(request: NextRequest, path = "") {
  const origin = process.env.API_URL || "http://localhost:8010";
  const url = new URL(`/workflows${path}`, origin);
  url.search = request.nextUrl.search;
  try {
    const response = await fetch(url, {
      method: request.method,
      ...(request.method === "POST" ? {headers: {"Content-Type": "application/json"}, body: await request.text()} : {}),
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok)
      return NextResponse.json(
        {
          detail:
            response.status === 404
              ? "Workflow not found."
              : response.status === 409
                ? "This review is no longer available. Refresh to see the latest decision."
                : response.status === 422
                  ? "Check the decision and review note."
                  : "Unable to load workflow data.",
        },
        { status: response.status },
      );
    return NextResponse.json(await response.json(), {
      headers: { "Cache-Control": "no-store" },
    });
  } catch {
    return NextResponse.json(
      {
        detail:
          "Cannot reach the Relay API. Check that the backend is running, then try again.",
      },
      { status: 502 },
    );
  }
}
