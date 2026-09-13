import { NextRequest, NextResponse } from "next/server";
import { codingProxy } from "@/lib/coding-proxy";
export async function GET(request: NextRequest, {params}: {params: Promise<{path: string[]}>}) {
  const {path} = await params;
  if (path.length !== 2 || path[1] !== "events")
    return NextResponse.json({detail: "Not found"}, {status: 404});
  return codingProxy(request, "/" + encodeURIComponent(path[0]) + "/events");
}
