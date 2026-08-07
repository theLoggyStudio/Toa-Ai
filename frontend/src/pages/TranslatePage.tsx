import { useCallback, useEffect, useRef, useState } from 'react';
import {
  confirmPayment,
  getAppConfig,
  getTask,
  initCheckout,
  resetServerSession,
  retryTask,
  startProcessing,
  subscribeTaskEvents,
  uploadImages,
} from '../api/client';
import { dedupeFiles } from '../utils/dedupeFiles';
import { FileUploadZone } from '../components/FileUploadZone';
import { Footer } from '../components/Footer';
import { HeroBanner } from '../components/HeroBanner';
import { PriceSummary } from '../components/PriceSummary';
import { TaskDashboardModal } from '../components/TaskDashboardModal';
import { TARGET_LANGUAGE_OPTIONS } from '../constants/languages';
import type { TargetLanguage, TranslationTask } from '../types/translation';

// Mascotte Toa (Chibie) : désactivée pour l'instant, le code reste en place.
// Repasser à true (et CHIBIE_ENABLED=true côté backend) pour la réactiver.
const TOA_FEATURE_ENABLED = false;

export function TranslatePage() {
  const [files, setFiles] = useState<File[]>([]);
  const [targetLanguage, setTargetLanguage] = useState<TargetLanguage>('fr');
  const [task, setTask] = useState<TranslationTask | null>(null);
  const [priceDisplayed, setPriceDisplayed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paymentDisabled, setPaymentDisabled] = useState(false);
  const [priceBase, setPriceBase] = useState(200);
  const [pricePerBubble, setPricePerBubble] = useState(25);
  const [uploadedFileKey, setUploadedFileKey] = useState<string | null>(null);
  const [dashboardModalOpen, setDashboardModalOpen] = useState(false);
  const [includeToa, setIncludeToa] = useState(TOA_FEATURE_ENABLED);

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeTaskIdRef = useRef<string | null>(null);
  const sseCloseRef = useRef<(() => void) | null>(null);

  const buildFileKey = (fileList: File[]) =>
    fileList
      .map((f) => `${f.name}:${f.size}:${f.lastModified}`)
      .join('|');

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    if (sseCloseRef.current) {
      sseCloseRef.current();
      sseCloseRef.current = null;
    }
    activeTaskIdRef.current = null;
  }, []);

  const resetSession = useCallback(async () => {
    stopPolling();
    setTask(null);
    setFiles([]);
    setPriceDisplayed(false);
    setUploadedFileKey(null);
    setDashboardModalOpen(false);
    setError(null);
    window.history.replaceState({}, '', window.location.pathname);
    try {
      await resetServerSession();
    } catch {
      /* backend peut être arrêté */
    }
  }, [stopPolling]);

  const pollTask = useCallback(
    async (taskId: string) => {
      if (activeTaskIdRef.current !== taskId) return;
      try {
        const updated = await getTask(taskId);
        if (activeTaskIdRef.current !== taskId) return;
        setTask(updated);
        if (updated.status === 'processing' || updated.status === 'paid') {
          pollTimerRef.current = setTimeout(() => pollTask(taskId), 2000);
        } else {
          stopPolling();
          setUploadedFileKey(null);
        }
      } catch (err) {
        if (activeTaskIdRef.current !== taskId) return;
        const msg = err instanceof Error ? err.message : '';
        if (msg.includes('Not Found') || msg.includes('404')) {
          setError(
            'Tâche introuvable sur le serveur. Relancez une nouvelle traduction.',
          );
        } else if (
          msg.includes('Failed to fetch') ||
          msg.includes('NetworkError')
        ) {
          setError(
            'Backend injoignable. Vérifiez que le serveur tourne sur http://127.0.0.1:9400',
          );
        }
        stopPolling();
      }
    },
    [stopPolling],
  );

  const startPolling = useCallback(
    (taskId: string) => {
      stopPolling();
      activeTaskIdRef.current = taskId;
      // Suivi temps réel en SSE ; repli sur le polling si le flux échoue.
      sseCloseRef.current = subscribeTaskEvents(
        taskId,
        (updated) => {
          if (activeTaskIdRef.current !== taskId) return;
          setTask(updated);
        },
        () => {
          if (activeTaskIdRef.current !== taskId) return;
          sseCloseRef.current = null;
          stopPolling();
          setUploadedFileKey(null);
        },
        () => {
          if (activeTaskIdRef.current !== taskId) return;
          sseCloseRef.current = null;
          pollTask(taskId);
        },
      );
    },
    [pollTask, stopPolling],
  );

  const handleRetry = useCallback(async () => {
    if (!task) return;
    setError(null);
    setLoading(true);
    setDashboardModalOpen(true);
    try {
      const { task: updated } = await retryTask(task.id);
      setTask(updated);
      startPolling(updated.id);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : 'Impossible de reprendre la tâche.',
      );
    } finally {
      setLoading(false);
    }
  }, [task, startPolling]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const returnTaskId = params.get('task_id');
    const cancelled = params.get('cancelled');
    const paidReturn = params.get('paid_return');

    getAppConfig()
      .then((cfg) => {
        setPaymentDisabled(cfg.paymentDisabled);
        setPriceBase(cfg.priceBaseCFA ?? 200);
        setPricePerBubble(cfg.pricePerBubbleCFA ?? 25);
      })
      .catch(() => {
        setPaymentDisabled(false);
        setPriceBase(200);
        setPricePerBubble(25);
      });

    if (returnTaskId && cancelled) {
      setError('Paiement annulé. Vous pouvez réessayer quand vous le souhaitez.');
      getTask(returnTaskId)
        .then((t) => {
          setTask(t);
          setPriceDisplayed(true);
        })
        .catch(() => {
          /* tâche expirée */
        });
      window.history.replaceState({}, '', window.location.pathname);
    } else if (returnTaskId && paidReturn) {
      setLoading(true);
      setDashboardModalOpen(true);
      confirmPayment(returnTaskId)
        .then(({ task: confirmed, paymentPending }) => {
          setTask(confirmed);
          setPriceDisplayed(true);
          if (paymentPending) {
            setError(
              'Paiement en attente de confirmation. Réessayez dans quelques secondes.',
            );
          } else if (
            confirmed.status === 'processing' ||
            confirmed.status === 'paid'
          ) {
            startPolling(returnTaskId);
          }
        })
        .catch((err) => {
          setError(
            err instanceof Error
              ? err.message
              : 'Impossible de confirmer le paiement.',
          );
        })
        .finally(() => {
          setLoading(false);
          window.history.replaceState({}, '', window.location.pathname);
        });
    } else {
      window.history.replaceState({}, '', window.location.pathname);
    }

    return () => stopPolling();
  }, [startPolling, stopPolling]);

  const handleFilesChange = (newFiles: File[]) => {
    const deduped = dedupeFiles(newFiles);
    if (
      task &&
      buildFileKey(deduped) !== buildFileKey(files) &&
      task.status !== 'processing' &&
      task.status !== 'paid'
    ) {
      stopPolling();
      setTask(null);
      setPriceDisplayed(false);
      setUploadedFileKey(null);
      setError(null);
    }
    setFiles(deduped);
  };

  const handleEvaluate = async () => {
    const uniqueFiles = dedupeFiles(files);
    if (uniqueFiles.length === 0) {
      setError('Ajoutez au moins une image.');
      return;
    }
    setLoading(true);
    stopPolling();
    setError(null);
    setPriceDisplayed(false);
    setTask(null);
    setUploadedFileKey(null);
    window.history.replaceState({}, '', window.location.pathname);

    try {
      await resetServerSession();
      if (uniqueFiles.length !== files.length) {
        setFiles(uniqueFiles);
      }
      const { task: newTask, paymentDisabled: noPayment } = await uploadImages(
        uniqueFiles,
        targetLanguage,
        includeToa,
      );
      setTask(newTask);
      setPaymentDisabled(noPayment);
      setPriceDisplayed(true);
      setUploadedFileKey(buildFileKey(uniqueFiles));
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "Erreur lors de l'upload.",
      );
    } finally {
      setLoading(false);
    }
  };

  const handleStartTest = async () => {
    if (!task || !priceDisplayed) return;
    const currentKey = buildFileKey(files);
    if (!uploadedFileKey || currentKey !== uploadedFileKey) {
      setError(
        "Les images ont changé. Cliquez d'abord sur « Évaluer le prix » pour envoyer les nouveaux fichiers.",
      );
      return;
    }
    setLoading(true);
    setError(null);
    setDashboardModalOpen(true);
    try {
      const { task: updated } = await startProcessing(task.id);
      setTask(updated);
      startPolling(task.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur au démarrage.');
    } finally {
      setLoading(false);
    }
  };

  const handlePay = async () => {
    if (!task || !priceDisplayed) return;
    const currentKey = buildFileKey(files);
    if (!uploadedFileKey || currentKey !== uploadedFileKey) {
      setError(
        "Les images ont changé. Cliquez d'abord sur « Évaluer le prix » pour envoyer les nouveaux fichiers.",
      );
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const { paymentUrl } = await initCheckout(task.id);
      window.location.href = paymentUrl;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Erreur lors du paiement.');
      setLoading(false);
    }
  };

  const canPay =
    !paymentDisabled &&
    priceDisplayed &&
    task?.status === 'pending_payment' &&
    task.amountCFA > 0;

  const canStartTest =
    paymentDisabled &&
    priceDisplayed &&
    task?.status === 'pending_payment';

  return (
    <div className="toa-page">
      <HeroBanner product="translate" />

      <main className="container pb-4" style={{ maxWidth: 760 }}>
        <div className="card toa-card border-0">
          <div className="card-body p-4">
            <div className="d-flex align-items-center justify-content-between gap-2 mb-1">
              <h1 className="h4 mb-0">Nouvelle traduction</h1>
              {paymentDisabled ? (
                <span className="badge toa-badge-test">Mode test</span>
              ) : (
                <span className="badge bg-warning text-dark">PayDunya test</span>
              )}
            </div>
            <p className="toa-text-muted small mb-4">
              Uploadez vos pages, payez, puis téléchargez votre PDF traduit.
            </p>

            <div className="mb-4">
              <label className="form-label">Langue cible</label>
              <select
                className="form-select toa-form-select"
                value={targetLanguage}
                onChange={(e) =>
                  setTargetLanguage(e.target.value as TargetLanguage)
                }
                disabled={
                  loading || (!!task && task.status !== 'pending_payment')
                }
              >
                {TARGET_LANGUAGE_OPTIONS.map((opt) => (
                  <option key={opt.code} value={opt.code}>
                    {opt.label}
                  </option>
                ))}
              </select>
              <p className="small toa-text-muted mt-2 mb-0">
                La langue source est détectée automatiquement par Cursor sur
                vos pages.
              </p>
            </div>

            <FileUploadZone
              files={files}
              onFilesChange={handleFilesChange}
              disabled={
                loading ||
                task?.status === 'processing' ||
                task?.status === 'paid'
              }
            />

            {TOA_FEATURE_ENABLED && (
              <div className="form-check mt-3">
                <input
                  className="form-check-input"
                  type="checkbox"
                  id="includeToa"
                  checked={includeToa}
                  onChange={(e) => setIncludeToa(e.target.checked)}
                  disabled={
                    loading ||
                    (!!task && task.status !== 'pending_payment')
                  }
                />
                <label className="form-check-label" htmlFor="includeToa">
                  Ajouter Toa (le Chibie)
                </label>
              </div>
            )}

            {task && (
              <button
                type="button"
                className="btn toa-btn-outline mt-3 w-100"
                onClick={resetSession}
                disabled={loading}
              >
                Nouvelle traduction
              </button>
            )}

            {!task && (
              <button
                type="button"
                className="btn toa-btn-primary mt-3 w-100"
                onClick={handleEvaluate}
                disabled={loading || files.length === 0}
              >
                {loading ? 'Envoi des images…' : 'Évaluer le prix'}
              </button>
            )}

            {priceDisplayed && task && (
              <>
                <PriceSummary
                  task={task}
                  priceBase={priceBase}
                  pricePerBubble={pricePerBubble}
                />
                <p className="small text-muted mb-0">
                  Tâche #{task.id.slice(0, 8)}
                </p>
                {canStartTest && (
                  <button
                    type="button"
                    className="btn toa-btn-success w-100 mt-2"
                    onClick={handleStartTest}
                    disabled={loading}
                  >
                    {loading ? 'Démarrage…' : 'Démarrer la traduction'}
                  </button>
                )}
                {canPay && (
                  <button
                    type="button"
                    className="btn toa-btn-success w-100 mt-2"
                    onClick={handlePay}
                    disabled={loading}
                  >
                    {loading ? 'Redirection…' : 'Payer avec PayDunya'}
                  </button>
                )}
              </>
            )}

            {error && (
              <div className="alert toa-alert-danger mt-3 mb-0" role="alert">
                {error}
              </div>
            )}
          </div>
        </div>

        {task && dashboardModalOpen && (
          <TaskDashboardModal
            task={task}
            open={dashboardModalOpen}
            onClose={() => setDashboardModalOpen(false)}
            onRetry={handleRetry}
          />
        )}

        <p className="text-center toa-meta small mt-4 mb-0">
          {priceBase} FCFA de base + {pricePerBubble} FCFA / bulle
        </p>
      </main>

      <Footer tagline="Traduction manga & manhwa" />
    </div>
  );
}

