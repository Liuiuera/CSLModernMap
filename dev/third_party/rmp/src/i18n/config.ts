import { defaultTranslation, LanguageCode, Translation } from '@railmapgen/rmg-translate';
import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';

const i18n = i18next.createInstance();
const catalogs = import.meta.glob<Record<string, unknown>>('./translations/*.json', { eager: true, import: 'default' });
const translations = Object.fromEntries(Object.entries(catalogs).map(([path, value]) =>
    [path.split('/').pop()!.replace('.json', ''), value]));
const resources = Object.fromEntries(Object.entries({ ...defaultTranslation, ...translations }).map(([key]) =>
    [key, { translation: { ...defaultTranslation[key as keyof typeof defaultTranslation], ...translations[key] } }]));
void i18n.use(initReactI18next).init({
    lng: new URLSearchParams(location.search).get('language') ?? 'zh-Hans',
    fallbackLng: 'en', resources, interpolation: { escapeValue: false },
});

export default i18n;

export const translateText = (translation: Translation): string =>
    i18n.languages.map(lang => translation[lang as LanguageCode]).find(name => name !== undefined) ??
    translation.en ??
    '(Translation Error)';
