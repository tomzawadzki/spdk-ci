import { BackendResponse } from './types';

const BASE_URL = '/checks-api/v1';
const TIMEOUT_MS = 10_000;

async function fetchWithTimeout(url: string, init?: RequestInit): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export async function fetchRuns(
  changeNumber: number,
  patchsetNumber: number,
): Promise<BackendResponse> {
  const url = `${BASE_URL}/changes/${changeNumber}/patchsets/${patchsetNumber}/runs`;
  const response = await fetchWithTimeout(url);
  if (!response.ok) throw new Error(`Backend error: ${response.status}`);
  return response.json();
}

export async function triggerCI(
  changeNumber: number,
  patchsetNumber: number,
): Promise<void> {
  const url = `${BASE_URL}/changes/${changeNumber}/patchsets/${patchsetNumber}/trigger`;
  const response = await fetchWithTimeout(url, { method: 'POST' });
  if (!response.ok) throw new Error(`Trigger failed: ${response.status}`);
}

export async function rerunCI(
  changeNumber: number,
  patchsetNumber: number,
): Promise<void> {
  const url = `${BASE_URL}/changes/${changeNumber}/patchsets/${patchsetNumber}/rerun`;
  const response = await fetchWithTimeout(url, { method: 'POST' });
  if (!response.ok) throw new Error(`Rerun failed: ${response.status}`);
}
