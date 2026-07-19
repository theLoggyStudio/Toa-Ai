import type {
  SourceLanguageCode,
  TargetLanguageCode,
} from '../constants/languages';

export type SourceLanguage = SourceLanguageCode;
export type TargetLanguage = TargetLanguageCode;
export type TaskStatus =
  | 'pending_payment'
  | 'paid'
  | 'processing'
  | 'completed'
  | 'failed';

export interface TranslationTask {
  id: string;
  originalImagesCount: number;
  billableBubblesCount?: number;
  sourceLanguage: SourceLanguage;
  targetLanguage: TargetLanguage;
  status: TaskStatus;
  amountCFA: number;
  includeToa?: boolean;
  pdfUrl?: string;
  partialPdfUrl?: string;
  progressPercent?: number;
  progressMessage?: string;
  errorMessage?: string;
}

export interface TextBlock {
  id: number;
  boundingBox: {
    x_min: number;
    y_min: number;
    x_max: number;
    y_max: number;
  };
  originalText: string;
  translatedText: string;
}

export interface AppConfig {
  paymentDisabled: boolean;
  priceBaseCFA: number;
  pricePerBubbleCFA: number;
}

export interface UploadResponse {
  task: TranslationTask;
  checkoutReady: boolean;
  paymentDisabled: boolean;
}

export interface StartProcessingResponse {
  task: TranslationTask;
  message: string;
}

export interface CheckoutResponse {
  paymentUrl: string;
}

export interface ConfirmPaymentResponse {
  task: TranslationTask;
  paymentPending?: boolean;
  alreadyStarted?: boolean;
}
