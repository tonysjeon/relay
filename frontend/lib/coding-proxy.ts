import { NextRequest, NextResponse } from "next/server";

export async function codingProxy(request: NextRequest, path = "") {
  const url = new URL("/coding-sessions" + path, process.env.API_URL || "http://localhost:8010");
  url.search = request.nextUrl.search;
  try {
    const response = await fetch(url, {cache: "no-store", signal: AbortSignal.timeout(8000)});
    if (!response.ok) return NextResponse.json({detail: "Unable to load coding activity."}, {status: response.status});
    return NextResponse.json(await response.json(), {headers: {"Cache-Control": "no-store"}});
  } catch {
    return NextResponse.json({detail: "Cannot reach Relay. Check the backend and try again."}, {status: 502});
  }
}
