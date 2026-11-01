// 共用按钮内容；文字更新时保留图标，避免播放进度刷新时重复创建节点。
import {iconPaths} from './control-icon-paths';
import './control-icons.css';

export type ControlIcon = keyof typeof iconPaths;

export function controlIcon(name: ControlIcon): SVGSVGElement {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.classList.add('preview-icon'); svg.setAttribute('viewBox', '0 0 1000 1000');
  svg.setAttribute('aria-hidden', 'true'); svg.setAttribute('focusable', 'false');
  const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', iconPaths[name]);
  svg.append(path); return svg;
}

export function setButtonContent(button: HTMLButtonElement, label: string, icon: ControlIcon): void {
  button.classList.add('preview-button');
  let caption = button.querySelector<HTMLSpanElement>(':scope > .preview-button-label');
  if (!caption || button.dataset.previewIcon !== icon) {
    caption = document.createElement('span'); caption.className = 'preview-button-label';
    button.replaceChildren(controlIcon(icon), caption);
    button.dataset.previewIcon = icon;
  }
  caption.textContent = label;
}
