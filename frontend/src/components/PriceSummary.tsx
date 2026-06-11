import type { TranslationTask } from '../types/translation';

interface PriceSummaryProps {
  task: TranslationTask | null;
  priceBase?: number;
  pricePerBubble?: number;
}

export function PriceSummary({
  task,
  priceBase = 200,
  pricePerBubble = 25,
}: PriceSummaryProps) {
  if (!task) return null;
  const bubbles = task.billableBubblesCount ?? 0;
  const bubblePart = bubbles * pricePerBubble;

  return (
    <div className="alert toa-alert-price mt-4" role="alert">
      <h5 className="alert-heading mb-2">Tarification validée</h5>
      <p className="mb-1">
        <strong>
          Total pour {bubbles} bulle{bubbles > 1 ? 's' : ''} : {task.amountCFA}{' '}
          FCFA
        </strong>
      </p>
      <small className="toa-text-muted d-block mb-1">
        Estimation via Cursor (échantillon de pages), puis traduction complète.
      </small>
      <small className="toa-text-muted">
        ({priceBase} FCFA de base
        {bubbles > 0 ? ` + ${bubblePart} FCFA (${bubbles} × ${pricePerBubble})` : ''}
        {' · '}
        {task.sourceLanguage === 'auto'
          ? 'auto (Cursor)'
          : task.sourceLanguage.toUpperCase()}{' '}
        → {task.targetLanguage.toUpperCase()})
      </small>
    </div>
  );
}
