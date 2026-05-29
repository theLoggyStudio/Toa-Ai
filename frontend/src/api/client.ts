import type {
  AppConfig,
  CheckoutResponse,
  SourceLanguage,
  StartProcessingResponse,
  TargetLanguage,
  TranslationTask,
  UploadResponse,
} from '../types/translation';

export const API_BASE =
  import.meta.env.VITE_API_URL?.replace(/\/$/, '') || 'http://127.0.0.1:8000';
const REQUEST_TIMEOUT_MS = 120_000;

const NO_CACHE: RequestInit = {
  cache: 'no-store',
  headers: { 'Cache-Control': 'no-cache', Pragma: 'no-cache' },
};

async function fetchWithTimeout(
  url: string,
  init?: RequestInit,
): Promise<Response> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error(
        `Le serveur met trop de temps à répondre. Vérifiez que le backend tourne sur ${API_BASE}`,
      );
    }
    if (err instanceof TypeError) {
      throw new Error(
        `Backend injoignable. Lancez "npm start" ou uvicorn sur ${API_BASE}`,
      );
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(detail || `Erreur HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function resetServerSession(): Promise<void> {
  const response = await fetchWithTimeout(`${API_BASE}/api/session/reset`, {
    method: 'POST',
    ...NO_CACHE,
  });
  await handleResponse<{ ok: boolean }>(response);
}

export async function getAppConfig(): Promise<AppConfig> {
  const response = await fetchWithTimeout(`${API_BASE}/api/config`, NO_CACHE);
  return handleResponse<AppConfig>(response);
}

export async function startProcessing(
  taskId: string,
): Promise<StartProcessingResponse> {
  const response = await fetchWithTimeout(`${API_BASE}/api/tasks/${taskId}/start`, {
    method: 'POST',
    ...NO_CACHE,
  });
  return handleResponse<StartProcessingResponse>(response);
}

export async function uploadImages(
  files: File[],
  sourceLanguage: SourceLanguage,
  targetLanguage: TargetLanguage,
): Promise<UploadResponse> {
  const formData = new FormData();
  files.forEach((file) => formData.append('images', file));
  formData.append('source_language', sourceLanguage);
  formData.append('target_language', targetLanguage);

  const response = await fetchWithTimeout(`${API_BASE}/api/tasks/upload`, {
    method: 'POST',
    body: formData,
    cache: 'no-store',
  });
  return handleResponse<UploadResponse>(response);
}

export async function initCheckout(taskId: string): Promise<CheckoutResponse> {
  const response = await fetchWithTimeout(`${API_BASE}/api/tasks/${taskId}/checkout`, {
    method: 'POST',
    ...NO_CACHE,
  });
  return handleResponse<CheckoutResponse>(response);
}

export async function getTask(taskId: string): Promise<TranslationTask> {
  const response = await fetchWithTimeout(
    `${API_BASE}/api/tasks/${taskId}?_=${Date.now()}`,
    NO_CACHE,
  );
  return handleResponse<TranslationTask>(response);
}

export function getPdfDownloadUrl(taskId: string): string {
  return `${API_BASE}/api/tasks/${taskId}/pdf?_=${Date.now()}`;
}
