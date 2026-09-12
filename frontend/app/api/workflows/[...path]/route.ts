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
