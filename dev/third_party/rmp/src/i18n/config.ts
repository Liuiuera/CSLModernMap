import { defaultTranslation, LanguageCode, Translation } from '@railmapgen/rmg-translate';
import i18next from 'i18next';
import { initReactI18next } from 'react-i18next';
import enTranslation from './translations/en.json';
import jaTranslation from './translations/ja.json';
import koTranslation from './translations/ko.json';
import zhHansTranslation from './translations/zh-Hans.json';
import zhHantTranslation from './translations/zh-Hant.json';

const i18n = i18next.createInstance();
const translations = { en: enTranslation, 'zh-Hans': zhHansTranslation, 'zh-Hant': zhHantTranslation, ja: jaTranslation, ko: koTranslation };
const resources = Object.fromEntries(Object.entries(defaultTranslation).map(([key, value]) => [key, { translation: { ...value, ...translations[key as keyof typeof translations] } }]));
void i18n.use(initReactI18next).init({
    lng: new URLSearchParams(location.search).get('language') ?? 'zh-Hans',
    fallbackLng: 'en', resources, interpolation: { escapeValue: false },
});

export default i18n;

export const translateText = (translation: Translation): string =>
    i18n.languages.map(lang => translation[lang as LanguageCode]).find(name => name !== undefined) ??
    translation.en ??
    '(Translation Error)';
