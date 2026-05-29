export const SOURCE_LANGUAGE_OPTIONS = [
  { code: 'auto', label: 'Détection automatique' },
  { code: 'ja', label: 'Japonais' },
  { code: 'ko', label: 'Coréen' },
  { code: 'zh', label: 'Chinois' },
  { code: 'en', label: 'Anglais' },
  { code: 'es', label: 'Espagnol' },
  { code: 'de', label: 'Allemand' },
  { code: 'fr', label: 'Français' },
  { code: 'pt', label: 'Portugais' },
  { code: 'ru', label: 'Russe' },
  { code: 'ar', label: 'Arabe' },
  { code: 'hi', label: 'Hindi' },
  { code: 'th', label: 'Thaï' },
  { code: 'vi', label: 'Vietnamien' },
  { code: 'id', label: 'Indonésien' },
] as const;

export const TARGET_LANGUAGE_OPTIONS = [
  { code: 'fr', label: 'Français' },
  { code: 'en', label: 'English' },
  { code: 'es', label: 'Español' },
  { code: 'de', label: 'Deutsch' },
  { code: 'pt', label: 'Português' },
  { code: 'it', label: 'Italiano' },
  { code: 'ar', label: 'العربية' },
  { code: 'zh', label: '中文' },
  { code: 'ru', label: 'Русский' },
  { code: 'ja', label: '日本語' },
  { code: 'ko', label: '한국어' },
  { code: 'hi', label: 'हिन्दी' },
  { code: 'tr', label: 'Türkçe' },
  { code: 'vi', label: 'Tiếng Việt' },
  { code: 'id', label: 'Bahasa Indonesia' },
  { code: 'pl', label: 'Polski' },
  { code: 'nl', label: 'Nederlands' },
] as const;

export type SourceLanguageCode =
  (typeof SOURCE_LANGUAGE_OPTIONS)[number]['code'];
export type TargetLanguageCode =
  (typeof TARGET_LANGUAGE_OPTIONS)[number]['code'];
