import { NextRequest, NextResponse } from "next/server";
import { proxy } from "@/lib/proxy";
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  if (
    path.length !== 1 ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(
      path[0],
    )
  )
    return NextResponse.json(
      { detail: "Workflow not found." },
      { status: 404 },
    );
  return proxy(request, `/${path[0]}`);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const { path } = await params;
  const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
  if (path.length !== 4 || !uuid.test(path[0]) || path[1] !== "steps" || !uuid.test(path[2]) || path[3] !== "approval")
    return NextResponse.json({ detail: "Approval not found." }, { status: 404 });
  const origin = request.headers.get("origin");
  if (origin) {
    let originHost: string;
    try { originHost = new URL(origin).host; }
    catch { return NextResponse.json({ detail: "Invalid origin." }, { status: 403 }); }
    if (originHost !== request.headers.get("host"))
      return NextResponse.json({ detail: "Request origin is not allowed." }, { status: 403 });
  }
  return proxy(request, "/" + path.join("/"));
}
