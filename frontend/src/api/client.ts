import type {
  AppConfig,
  CheckoutResponse,
  ConfirmPaymentResponse,
  StartProcessingResponse,
  TargetLanguage,
  TranslationTask,
  UploadResponse,
} from '../types/translation';

export const API_BASE =
  import.meta.env.VITE_API_URL?.replace(/\/$/, '') || 'http://127.0.0.1:9400';
const REQUEST_TIMEOUT_MS = 120_000;
const UPLOAD_TIMEOUT_MS = 600_000;

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
    try {
      const parsed = JSON.parse(detail) as { detail?: string | unknown[] };
      if (typeof parsed.detail === 'string') {
        throw new Error(parsed.detail);
      }
    } catch (err) {
      if (err instanceof Error && err.message !== detail) {
        throw err;
      }
    }
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
  targetLanguage: TargetLanguage,
  includeToa = true,
): Promise<UploadResponse> {
  const formData = new FormData();
  files.forEach((file) => formData.append('images', file));
  formData.append('target_language', targetLanguage);
  formData.append('include_toa', includeToa ? 'true' : 'false');

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/tasks/upload`, {
      method: 'POST',
      body: formData,
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error(
        "L'envoi des images prend trop de temps. Réessayez avec moins de pages.",
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
  return handleResponse<UploadResponse>(response);
}

export async function initCheckout(taskId: string): Promise<CheckoutResponse> {
  const response = await fetchWithTimeout(`${API_BASE}/api/tasks/${taskId}/checkout`, {
    method: 'POST',
    ...NO_CACHE,
  });
  return handleResponse<CheckoutResponse>(response);
}

export async function confirmPayment(
  taskId: string,
): Promise<ConfirmPaymentResponse> {
  const response = await fetchWithTimeout(
    `${API_BASE}/api/tasks/${taskId}/confirm-payment`,
    {
      method: 'POST',
      ...NO_CACHE,
    },
  );
  return handleResponse<ConfirmPaymentResponse>(response);
}

export async function getTask(taskId: string): Promise<TranslationTask> {
  const response = await fetchWithTimeout(
    `${API_BASE}/api/tasks/${taskId}?_=${Date.now()}`,
    NO_CACHE,
  );
  return handleResponse<TranslationTask>(response);
}

export async function retryTask(
  taskId: string,
): Promise<StartProcessingResponse> {
  const response = await fetchWithTimeout(
    `${API_BASE}/api/tasks/${taskId}/retry`,
    {
      method: 'POST',
      ...NO_CACHE,
    },
  );
  return handleResponse<StartProcessingResponse>(response);
}

/**
 * Suivi temps réel via Server-Sent Events.
 * Retourne une fonction d'arrêt ; onFallback est appelé si le flux SSE échoue
 * (l'appelant repasse alors au polling classique).
 */
export function subscribeTaskEvents(
  taskId: string,
  onTask: (task: TranslationTask) => void,
  onDone: () => void,
  onFallback: () => void,
): () => void {
  const source = new EventSource(`${API_BASE}/api/tasks/${taskId}/events`);
  let receivedAny = false;

  source.onmessage = (event) => {
    receivedAny = true;
    try {
      const task = JSON.parse(event.data) as TranslationTask;
      onTask(task);
      if (task.status !== 'processing' && task.status !== 'paid') {
        source.close();
        onDone();
      }
    } catch {
      /* message SSE illisible : ignoré */
    }
  };

  source.onerror = () => {
    source.close();
    // Le serveur ferme le flux en fin de tâche : ce n'est un échec que si
    // aucun événement n'a été reçu.
    if (!receivedAny) {
      onFallback();
    } else {
      onDone();
    }
  };

  return () => source.close();
}

export function getPdfDownloadUrl(taskId: string): string {
  return `${API_BASE}/api/tasks/${taskId}/pdf?_=${Date.now()}`;
}

export function getPartialPdfDownloadUrl(taskId: string): string {
  return `${API_BASE}/api/tasks/${taskId}/pdf/partial?_=${Date.now()}`;
}

export async function uploadRestoreImage(
  file: File,
  options: string[] = ['tears'],
): Promise<UploadResponse> {
  const formData = new FormData();
  formData.append('image', file);
  for (const opt of options) {
    formData.append('options', opt);
  }

  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), UPLOAD_TIMEOUT_MS);
  let response: Response;
  try {
    response = await fetch(`${API_BASE}/api/restore/upload`, {
      method: 'POST',
      body: formData,
      cache: 'no-store',
      signal: controller.signal,
    });
  } catch (err) {
    if (err instanceof Error && err.name === 'AbortError') {
      throw new Error("L'envoi de l'image prend trop de temps.");
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
  return handleResponse<UploadResponse>(response);
}

export async function updateRestoreOptions(
  taskId: string,
  options: string[],
): Promise<UploadResponse> {
  const formData = new FormData();
  for (const opt of options) {
    formData.append('options', opt);
  }
  const response = await fetch(`${API_BASE}/api/restore/${taskId}/options`, {
    method: 'POST',
    body: formData,
    cache: 'no-store',
  });
  return handleResponse<UploadResponse>(response);
}

export function getRestoredImageUrl(taskId: string): string {
  return `${API_BASE}/api/restore/${taskId}/image?_=${Date.now()}`;
}
