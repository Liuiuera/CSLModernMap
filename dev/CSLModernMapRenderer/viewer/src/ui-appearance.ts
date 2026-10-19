// 同步预览器的界面外观设置。
// 此模块不依赖地图数据，也不修改场景或主题。
const query = new URLSearchParams(location.search);
const desktop = query.get('desktopUi') === '1' && !(globalThis as any).__CSL_SCENE__;

export function setUiAppearance(appearance: unknown): void {
  if (!desktop || (globalThis as any).__CSL_SCENE__) return;
  document.documentElement.dataset.uiAppearance = appearance === 'dark' ? 'dark' : 'light';
}

setUiAppearance(query.get('uiAppearance'));
