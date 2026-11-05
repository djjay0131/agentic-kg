/** Same-origin health proxy (ADR-0012). See `src/lib/upstream.ts`. */

import { NextRequest } from 'next/server';

import { proxyToApi } from '@/lib/upstream';

export const dynamic = 'force-dynamic';

export function GET(request: NextRequest) {
  return proxyToApi(request);
}
