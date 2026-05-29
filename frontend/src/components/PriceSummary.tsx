import type { TranslationTask } from '../types/translation';

interface PriceSummaryProps {
  task: TranslationTask | null;
  pricePerBubble?: number;
}

export function PriceSummary({
  task,
  pricePerBubble = 75,
}: PriceSummaryProps) {
  if (!task) return null;
  const bubbles = task.billableBubblesCount ?? 0;

  return (
    <div className="alert toa-alert-price mt-4" role="alert">
      <h5 className="alert-heading mb-2">Tarification validée</h5>
      <p className="mb-1">
        <strong>
          Total pour {bubbles} bulle{bubbles > 1 ? 's' : ''} : {task.amountCFA}{' '}
          FCFA
        </strong>
      </p>
      <small className="toa-text-muted">
        ({pricePerBubble} FCFA par bulle · {task.sourceLanguage.toUpperCase()} →{' '}
        {task.targetLanguage.toUpperCase()})
      </small>
    </div>
  );
}
