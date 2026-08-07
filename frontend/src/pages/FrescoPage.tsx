import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  API_BASE,
  confirmPayment,
  getAppConfig,
  getRestoredImageUrl,
  getTask,
  initCheckout,
  resetServerSession,
  startProcessing,
  subscribeTaskEvents,
  uploadRestoreImage,
} from '../api/client';
import { FileUploadZone } from '../components/FileUploadZone';
import { Footer } from '../components/Footer';
import { HeroBanner } from '../components/HeroBanner';
import type { TranslationTask } from '../types/translation';

export function FrescoPage() {
  const [files, setFiles] = useState<File[]>([]);
  const [task, setTask] = useState<TranslationTask | null>(null);
  const [priceDisplayed, setPriceDisplayed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paymentDisabled, setPaymentDisabled] = useState(false);
  const [priceMin, setPriceMin] = useState(250);
  const [priceMax, setPriceMax] = useState(1000);
  const [localPreview, setLocalPreview] = useState<string | null>(null);

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeTaskIdRef = useRef<string | null>(null);
  const sseCloseRef = useRef<(() => void) | null>(null);

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

  const pollTask = useCallback(
    async (taskId: string) => {
      if (activeTaskIdRef.current !== taskId) return;
      try {
        const updated = await getTask(taskId);
        if (activeTaskIdRef.current !== taskId) return;
        setTask(updated);
        if (updated.status === 'processing' || updated.status === 'paid') {
          pollTimerRef.current = setTimeout(() => pollTask(taskId), 1500);
        } else {
          stopPolling();
        }
      } catch {
        stopPolling();
      }
    },
    [stopPolling],
  );

  const startPolling = useCallback(
    (taskId: string) => {
      stopPolling();
      activeTaskIdRef.current = taskId;
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

  const resetSession = useCallback(async () => {
    stopPolling();
    setTask(null);
    setFiles([]);
    setPriceDisplayed(false);
    setError(null);
    setLocalPreview(null);
    window.history.replaceState({}, '', window.location.pathname);
    try {
      await resetServerSession();
    } catch {
      /* backend peut être arrêté */
    }
  }, [stopPolling]);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const returnTaskId = params.get('task_id');
    const cancelled = params.get('cancelled');
    const paidReturn = params.get('paid_return');

    getAppConfig()
      .then((cfg) => {
        setPaymentDisabled(cfg.paymentDisabled);
        setPriceMin(cfg.eclatPriceMinCFA ?? 250);
        setPriceMax(cfg.eclatPriceMaxCFA ?? 1000);
      })
      .catch(() => {
        setPaymentDisabled(false);
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
    }

    return () => stopPolling();
  }, [startPolling, stopPolling]);

  useEffect(() => {
    if (files.length === 0) {
      setLocalPreview(null);
      return;
    }
    const url = URL.createObjectURL(files[0]);
    setLocalPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [files]);

  const handleFilesChange = (newFiles: File[]) => {
    const last = newFiles.length > 0 ? [newFiles[newFiles.length - 1]] : [];
    if (
      task &&
      task.status !== 'processing' &&
      task.status !== 'paid' &&
      task.status !== 'completed'
    ) {
      stopPolling();
      setTask(null);
      setPriceDisplayed(false);
      setError(null);
    }
    setFiles(last);
  };

  const handleEvaluate = async () => {
    if (files.length === 0) {
      setError('Ajoutez une photo à restaurer.');
      return;
    }
    setLoading(true);
    stopPolling();
    setError(null);
    setPriceDisplayed(false);
    setTask(null);
    window.history.replaceState({}, '', window.location.pathname);

    try {
      await resetServerSession();
      const { task: newTask, paymentDisabled: noPayment } =
        await uploadRestoreImage(files[0]);
      setTask(newTask);
      setPaymentDisabled(noPayment);
      setPriceDisplayed(true);
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
    setLoading(true);
    setError(null);
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

  const originalUrl = useMemo(() => {
    if (task?.id) {
      return `${API_BASE}/api/restore/${task.id}/original?_=${Date.now()}`;
    }
    return localPreview;
  }, [task?.id, localPreview]);

  const restoredUrl =
    task?.status === 'completed' && task.id
      ? getRestoredImageUrl(task.id)
      : null;

  const megapixels =
    task?.imageWidth && task?.imageHeight
      ? (task.imageWidth * task.imageHeight) / 1_000_000
      : null;

  return (
    <div className="toa-page toa-page--fresco">
      <HeroBanner product="fresco" />

      <main className="container pb-4" style={{ maxWidth: 760 }}>
        <div className="card toa-card border-0">
          <div className="card-body p-4">
            <div className="d-flex align-items-center justify-content-between gap-2 mb-1">
              <h1 className="h4 mb-0">Fresco</h1>
              {paymentDisabled ? (
                <span className="badge toa-badge-test">Mode test</span>
              ) : (
                <span className="badge bg-warning text-dark">PayDunya test</span>
              )}
            </div>
            <p className="toa-text-muted small mb-4">
              Déposez une photo abîmée : couleurs, contraste et netteté sont
              restaurés. Tarif selon la taille ({priceMin}–{priceMax} FCFA).
            </p>

            <FileUploadZone
              product="fresco"
              files={files}
              onFilesChange={handleFilesChange}
              disabled={
                loading ||
                task?.status === 'processing' ||
                task?.status === 'paid'
              }
            />

            {task && (
              <button
                type="button"
                className="btn toa-btn-outline mt-3 w-100"
                onClick={resetSession}
                disabled={loading}
              >
                Nouvelle photo
              </button>
            )}

            {!task && (
              <button
                type="button"
                className="btn toa-btn-primary mt-3 w-100"
                onClick={handleEvaluate}
                disabled={loading || files.length === 0}
              >
                {loading ? 'Analyse…' : 'Estimer le prix'}
              </button>
            )}

            {priceDisplayed && task && (
              <div className="toa-fresco-price mt-3 p-3 rounded">
                <div className="d-flex justify-content-between align-items-baseline">
                  <span className="fw-semibold">Estimation</span>
                  <span className="h5 mb-0">{task.amountCFA} FCFA</span>
                </div>
                {megapixels != null && (
                  <p className="small toa-text-muted mb-0 mt-1">
                    {task.imageWidth}×{task.imageHeight} px (
                    {megapixels.toFixed(2)} MP)
                  </p>
                )}
                <p className="small text-muted mb-0 mt-1">
                  Tâche #{task.id.slice(0, 8)}
                </p>

                {canStartTest && (
                  <button
                    type="button"
                    className="btn toa-btn-success w-100 mt-3"
                    onClick={handleStartTest}
                    disabled={loading}
                  >
                    {loading ? 'Démarrage…' : 'Restaurer (mode test)'}
                  </button>
                )}
                {canPay && (
                  <button
                    type="button"
                    className="btn toa-btn-success w-100 mt-3"
                    onClick={handlePay}
                    disabled={loading}
                  >
                    {loading ? 'Redirection…' : 'Payer avec PayDunya'}
                  </button>
                )}

                {(task.status === 'processing' || task.status === 'paid') && (
                  <p className="small mt-3 mb-0">
                    {task.progressMessage || 'Restauration en cours…'} (
                    {task.progressPercent ?? 0}%)
                  </p>
                )}
              </div>
            )}

            {(originalUrl || restoredUrl) && (
              <div className="toa-fresco-compare mt-4">
                <div className="row g-3">
                  <div className="col-6">
                    <p className="small text-center mb-2 fw-semibold">Avant</p>
                    {originalUrl ? (
                      <img
                        src={originalUrl}
                        alt="Original"
                        className="toa-fresco-compare__img"
                      />
                    ) : null}
                  </div>
                  <div className="col-6">
                    <p className="small text-center mb-2 fw-semibold">Après</p>
                    {restoredUrl ? (
                      <img
                        src={restoredUrl}
                        alt="Restauré"
                        className="toa-fresco-compare__img"
                      />
                    ) : (
                      <div className="toa-fresco-compare__placeholder">
                        {task?.status === 'processing'
                          ? 'En cours…'
                          : 'Après paiement'}
                      </div>
                    )}
                  </div>
                </div>
                {restoredUrl && (
                  <a
                    className="btn toa-btn-primary w-100 mt-3"
                    href={restoredUrl}
                    download
                  >
                    Télécharger l’image restaurée
                  </a>
                )}
              </div>
            )}

            {error && (
              <div className="alert toa-alert-danger mt-3 mb-0" role="alert">
                {error}
              </div>
            )}
          </div>
        </div>

        <p className="text-center toa-meta small mt-4 mb-0">
          {priceMin}–{priceMax} FCFA selon la taille (mégapixels)
        </p>
      </main>

      <Footer product="fresco" tagline="Fresco — restauration photo" />
    </div>
  );
}
