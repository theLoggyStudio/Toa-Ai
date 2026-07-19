import type { TranslationTask } from '../types/translation';
import { getPartialPdfDownloadUrl, getPdfDownloadUrl } from '../api/client';

interface TaskDashboardProps {
  task: TranslationTask;
  /** Dans la modale : pas de marge/card externe dupliquée */
  embedded?: boolean;
  /** Relance une tâche échouée (reprise depuis le checkpoint) */
  onRetry?: () => void;
}

const STATUS_LABELS: Record<TranslationTask['status'], string> = {
  pending_payment: 'En attente de paiement',
  paid: 'Paiement confirmé',
  processing: 'Traduction en cours…',
  completed: 'Terminé',
  failed: 'Échec',
};

const STATUS_VARIANT: Record<TranslationTask['status'], string> = {
  pending_payment: 'warning',
  paid: 'info',
  processing: 'primary',
  completed: 'success',
  failed: 'danger',
};

function resolveProgress(task: TranslationTask): number {
  if (task.status === 'completed') return 100;
  if (task.status === 'failed') return task.progressPercent ?? 0;
  if (typeof task.progressPercent === 'number' && task.progressPercent > 0) {
    return task.progressPercent;
  }
  if (task.status === 'processing') return 15;
  if (task.status === 'paid') return 30;
  if (task.status === 'pending_payment') return 10;
  return 0;
}

export function TaskDashboard({
  task,
  embedded = false,
  onRetry,
}: TaskDashboardProps) {
  const progress = resolveProgress(task);
  const isActive = task.status === 'processing' || task.status === 'paid';

  return (
    <div
      className={`card toa-card toa-dashboard border-0 ${embedded ? '' : 'mt-4'}`}
    >
      <div className="card-header d-flex justify-content-between align-items-center">
        <span>Tableau de bord — Tâche #{task.id.slice(0, 8)}</span>
        <span className={`badge text-bg-${STATUS_VARIANT[task.status]}`}>
          {STATUS_LABELS[task.status]}
        </span>
      </div>
      <div className="card-body">
        <div className="mb-3">
          <label className="form-label small toa-text-muted">Progression</label>
          <div className="progress toa-progress" role="progressbar" aria-valuenow={progress}>
            <div
              className={`progress-bar ${isActive ? 'progress-bar-striped progress-bar-animated' : ''}`}
              style={{ width: `${progress}%` }}
            >
              {progress}%
            </div>
          </div>
          {task.progressMessage && (
            <p className="small toa-text-muted mt-2 mb-0">{task.progressMessage}</p>
          )}
        </div>

        <ul className="list-unstyled mb-3">
          <li>
            <strong>Pages :</strong> {task.originalImagesCount}
          </li>
          <li>
            <strong>Bulles :</strong> {task.billableBubblesCount ?? 0}
          </li>
          <li>
            <strong>Montant :</strong> {task.amountCFA} FCFA
          </li>
          <li>
            <strong>Langues :</strong>{' '}
            {task.sourceLanguage === 'auto'
              ? 'auto (Cursor)'
              : task.sourceLanguage}{' '}
            → {task.targetLanguage}
          </li>
        </ul>

        {task.status === 'completed' && (
          <a
            href={getPdfDownloadUrl(task.id)}
            className="btn toa-btn-success"
            download={`toa-ai-${task.id.slice(0, 8)}.pdf`}
            key={task.id}
          >
            Télécharger le PDF traduit
          </a>
        )}

        {isActive && task.partialPdfUrl && (
          <a
            href={getPartialPdfDownloadUrl(task.id)}
            className="btn toa-btn-outline btn-sm"
            download={`toa-ai-${task.id.slice(0, 8)}-partiel.pdf`}
          >
            Télécharger le PDF partiel (pages déjà traduites)
          </a>
        )}

        {task.status === 'failed' && (
          <div className="toa-alert-danger p-3 rounded">
            <p className="mb-1">Le traitement a échoué.</p>
            {task.errorMessage && (
              <p className="small mb-2">
                <strong>Détail :</strong> {task.errorMessage}
              </p>
            )}
            {onRetry && (
              <button
                type="button"
                className="btn toa-btn-primary btn-sm"
                onClick={onRetry}
              >
                Reprendre là où ça s'est arrêté
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
