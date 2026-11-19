'use strict';
const frames = [...document.querySelectorAll('iframe')];
const loaded = [false, false];
let state;
const standard = {pixelScale: 1, lineDetail: 'standard', polygonDetail: 'standard'};
function report(type, details = {}) {
  window.chrome?.webview?.postMessage(JSON.stringify({type, ...details}));
}
function command(index, value) {
  return frames[index].contentWindow.cslPreview.dispatch(value).catch(error => {
    document.querySelector('#error').textContent = error.message;
    report('qualityDemoError', {message: error.message});
  });
}
function update() {
  if (!state) return;
  document.querySelector('#baseline').textContent = state.baselineLabel;
  document.querySelector('#draft').textContent = state.draftLabel;
  frames[0].title = state.baselineLabel; frames[1].title = state.draftLabel;
  document.body.classList.toggle('dark', state.appearance === 'dark');
  if (!loaded.every(Boolean)) return;
  for (const index of [0, 1]) {
    void command(index, {type: 'setLanguage', language: state.language});
    void command(index, {type: 'setPreviewQuality', requestId: `sample-${index}`, quality: index ? state.quality : standard});
  }
}
window.chrome?.webview?.addEventListener('message', event => {
  if (event.data.type === 'setQualityDemo') { state = event.data; update(); }
});
window.addEventListener('message', event => {
  if (event.origin !== location.origin) return;
  const index = frames.findIndex(frame => frame.contentWindow === event.source);
  if (index < 0) return;
  const message = event.data;
  if ((message.type === 'scenePresentationReady' ||
      (message.type === 'sceneLoaded' && message.performance?.completion === 'idle')) && !loaded[index]) {
    loaded[index] = true;
    if (loaded.every(Boolean)) { update(); report('qualityDemoReady'); }
  } else if (message.type === 'sampleCamera' && loaded.every(Boolean)) {
    const {center, zoom, bearing, pitch} = message;
    void command(1-index, {type: 'setSampleCamera', options: {center, zoom, bearing, pitch}});
  } else if (message.type === 'renderError' || message.type === 'previewStartupError') {
    document.querySelector('#error').textContent = message.message;
    report('qualityDemoError', {message: message.message});
  } else if (message.type === 'previewQualityApplied') {
    report('qualityDemoApplied', {index, outcome: message.outcome});
  }
});
