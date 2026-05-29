import { useCallback, useEffect, useRef, useState } from 'react';
import {
  API_BASE,
  getAppConfig,
  getTask,
  initCheckout,
  resetServerSession,
  startProcessing,
  uploadImages,
} from './api/client';
import { FileUploadZone } from './components/FileUploadZone';
import { Footer } from './components/Footer';
import { HeroBanner } from './components/HeroBanner';
import { PriceSummary } from './components/PriceSummary';
import { TaskDashboard } from './components/TaskDashboard';
import {
  SOURCE_LANGUAGE_OPTIONS,
  TARGET_LANGUAGE_OPTIONS,
} from './constants/languages';
import type {
  SourceLanguage,
  TargetLanguage,
  TranslationTask,
} from './types/translation';

function App() {
  const [files, setFiles] = useState<File[]>([]);
  const [sourceLanguage, setSourceLanguage] = useState<SourceLanguage>('auto');
  const [targetLanguage, setTargetLanguage] = useState<TargetLanguage>('fr');
  const [task, setTask] = useState<TranslationTask | null>(null);
  const [priceDisplayed, setPriceDisplayed] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [paymentDisabled, setPaymentDisabled] = useState(false);
  const [pricePerBubble, setPricePerBubble] = useState(75);
  const [uploadedFileKey, setUploadedFileKey] = useState<string | null>(null);

  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const activeTaskIdRef = useRef<string | null>(null);

  const buildFileKey = (fileList: File[]) =>
    fileList
      .map((f) => `${f.name}:${f.size}:${f.lastModified}`)
      .join('|');

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    activeTaskIdRef.current = null;
  }, []);

  const resetSession = useCallback(async () => {
    stopPolling();
    setTask(null);
    setFiles([]);
    setPriceDisplayed(false);
    setUploadedFileKey(null);
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
            `Backend injoignable. Vérifiez que le serveur tourne sur ${API_BASE}`,
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
      pollTask(taskId);
    },
    [pollTask, stopPolling],
  );

  useEffect(() => {
    getAppConfig()
      .then((cfg) => {
        setPaymentDisabled(cfg.paymentDisabled);
        setPricePerBubble(cfg.pricePerBubbleCFA || 75);
      })
      .catch(() => {
        setPaymentDisabled(false);
        setPricePerBubble(75);
      });
    window.history.replaceState({}, '', window.location.pathname);
    return () => stopPolling();
  }, [stopPolling]);

  const handleFilesChange = (newFiles: File[]) => {
    if (
      task &&
      buildFileKey(newFiles) !== buildFileKey(files) &&
      task.status !== 'processing' &&
      task.status !== 'paid'
    ) {
      stopPolling();
      setTask(null);
      setPriceDisplayed(false);
      setUploadedFileKey(null);
      setError(null);
    }
    setFiles(newFiles);
  };

  const handleEvaluate = async () => {
    if (files.length === 0) {
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
      const { task: newTask, paymentDisabled: noPayment } = await uploadImages(
        files,
        sourceLanguage,
        targetLanguage,
      );
      setTask(newTask);
      setPaymentDisabled(noPayment);
      setPriceDisplayed(true);
      setUploadedFileKey(buildFileKey(files));
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

  return (
    <div className="toa-page">
      <HeroBanner />

      <main className="container pb-4" style={{ maxWidth: 760 }}>
        <div className="card toa-card border-0">
          <div className="card-body p-4">
            <div className="d-flex align-items-center justify-content-between gap-2 mb-1">
              <h1 className="h4 mb-0">Nouvelle traduction</h1>
              {paymentDisabled && (
                <span className="badge toa-badge-test">Mode test</span>
              )}
            </div>
            <p className="toa-text-muted small mb-4">
              Uploadez vos pages, payez, puis téléchargez votre PDF traduit.
            </p>

            <div className="row g-3 mb-4">
              <div className="col-md-6">
                <label className="form-label">Langue source</label>
                <select
                  className="form-select toa-form-select"
                  value={sourceLanguage}
                  onChange={(e) =>
                    setSourceLanguage(e.target.value as SourceLanguage)
                  }
                  disabled={
                    loading ||
                    (!!task && task.status !== 'pending_payment')
                  }
                >
                  {SOURCE_LANGUAGE_OPTIONS.map((opt) => (
                    <option key={opt.code} value={opt.code}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="col-md-6">
                <label className="form-label">Langue cible</label>
                <select
                  className="form-select toa-form-select"
                  value={targetLanguage}
                  onChange={(e) =>
                    setTargetLanguage(e.target.value as TargetLanguage)
                  }
                  disabled={
                    loading ||
                    (!!task && task.status !== 'pending_payment')
                  }
                >
                  {TARGET_LANGUAGE_OPTIONS.map((opt) => (
                    <option key={opt.code} value={opt.code}>
                      {opt.label}
                    </option>
                  ))}
                </select>
              </div>
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
                <PriceSummary task={task} pricePerBubble={pricePerBubble} />
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
                    {loading
                      ? 'Démarrage…'
                      : 'Lancer la traduction (mode test)'}
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

        {task && (task.status !== 'pending_payment' || priceDisplayed) && (
          <TaskDashboard task={task} />
        )}

        <p className="text-center toa-meta small mt-4 mb-0">
          {pricePerBubble} FCFA / bulle
        </p>
      </main>

      <Footer />
    </div>
  );
}

export default App;
