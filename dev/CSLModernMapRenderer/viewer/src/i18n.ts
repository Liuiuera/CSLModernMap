// 加载预览器语言资源并切换界面语言。
import i18next from 'i18next';
const catalogs = import.meta.glob<Record<string, string>>('./locales/*.json', { eager: true, import: 'default' });
const resources = Object.fromEntries(Object.entries(catalogs).map(([path, translation]) =>
  [path.split('/').pop()!.replace('.json', ''), { translation }]));

export const i18n = i18next.createInstance();
void i18n.init({
  lng: document.documentElement.lang || 'zh-CN',
  fallbackLng: 'zh-CN',
  resources,
  supportedLngs: Object.keys(resources),
  initAsync: false,
  interpolation: { escapeValue: false },
});

export async function setLanguage(language: string): Promise<void> {
  await i18n.changeLanguage(language);
  document.documentElement.lang = language;
  if (!(globalThis as any).__CSL_SCENE__) document.title = i18n.t('preview_title');
}
