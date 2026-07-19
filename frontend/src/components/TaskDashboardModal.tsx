import type { TranslationTask } from '../types/translation';
import { TaskDashboard } from './TaskDashboard';

interface TaskDashboardModalProps {
  task: TranslationTask;
  open: boolean;
  onClose: () => void;
  onRetry?: () => void;
}

export function TaskDashboardModal({
  task,
  open,
  onClose,
  onRetry,
}: TaskDashboardModalProps) {
  const canClose =
    task.status === 'completed' ||
    task.status === 'failed' ||
    task.status === 'pending_payment';

  if (!open) {
    return null;
  }

  return (
    <>
      <div
        className="modal fade show d-block"
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-labelledby="toaDashboardModalTitle"
      >
        <div className="modal-dialog modal-dialog-centered modal-lg">
          <div className="modal-content toa-card border-0">
            <div className="modal-header border-0 pb-0">
              <h5 className="modal-title h6 mb-0" id="toaDashboardModalTitle">
                Suivi de la traduction
              </h5>
              {canClose && (
                <button
                  type="button"
                  className="btn-close"
                  aria-label="Fermer"
                  onClick={onClose}
                />
              )}
            </div>
            <div className="modal-body pt-2">
              <TaskDashboard task={task} embedded onRetry={onRetry} />
            </div>
          </div>
        </div>
      </div>
      <div className="modal-backdrop fade show" />
    </>
  );
}
