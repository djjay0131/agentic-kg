/** Same-origin API proxy (ADR-0011). See `src/lib/upstream.ts`. */

import { NextRequest } from 'next/server';

import { proxyToApi } from '@/lib/upstream';

export const dynamic = 'force-dynamic';

export function GET(request: NextRequest) {
  return proxyToApi(request);
}

export function POST(request: NextRequest) {
  return proxyToApi(request);
}

export function PUT(request: NextRequest) {
  return proxyToApi(request);
}

export function PATCH(request: NextRequest) {
  return proxyToApi(request);
}

export function DELETE(request: NextRequest) {
  return proxyToApi(request);
}
