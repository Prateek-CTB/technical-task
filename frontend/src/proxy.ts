import { NextResponse, type NextRequest } from "next/server";

/**
 * Same-origin API proxy. The browser calls `/api/v1/*` on this server and we forward it to the FastAPI
 * backend named by BACKEND_URL, read at request time (so it can be changed without rebuilding).
 * The backend address is server-side only; no secrets pass through the frontend.
 */
export function proxy(request: NextRequest) {
  const backend = (process.env.BACKEND_URL ?? "http://127.0.0.1:8000").replace(/\/$/, "");
  const { pathname, search } = request.nextUrl;
  return NextResponse.rewrite(new URL(`${pathname}${search}`, backend));
}

export const config = { matcher: "/api/v1/:path*" };
