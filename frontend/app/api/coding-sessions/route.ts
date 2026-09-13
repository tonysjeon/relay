import { NextRequest } from "next/server";
import { codingProxy } from "@/lib/coding-proxy";
export async function GET(request: NextRequest) {
  return codingProxy(request);
}
