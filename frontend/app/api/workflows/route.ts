import { NextRequest } from "next/server";
import { proxy } from "@/lib/proxy";
export function GET(request: NextRequest) {
  return proxy(request);
}
