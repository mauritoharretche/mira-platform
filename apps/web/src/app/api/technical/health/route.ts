import { NextResponse } from "next/server";

import { probeApiReadiness } from "@/lib/api-health";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export async function GET() {
  const result = await probeApiReadiness();
  return NextResponse.json(
    { status: result.status },
    {
      status: result.httpStatus,
      headers: { "Cache-Control": "no-store" },
    },
  );
}
