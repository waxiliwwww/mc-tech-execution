/* Local-file friendly: no fetch, build step, tracking, or external dependencies. */
document.querySelectorAll('pre.command').forEach(block => {
  const button = document.createElement('button');
  button.type = 'button'; button.className = 'copy'; button.textContent = '复制命令';
  button.setAttribute('aria-label', '复制本段命令');
  button.addEventListener('click', async () => {
    const code = block.querySelector('code');
    let copied = false;
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(code.textContent);
        copied = true;
      }
    } catch { /* Restricted file-viewers can still select and copy the text. */ }
    if (!copied) {
      const range = document.createRange(); range.selectNodeContents(code);
      const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
      try { copied = document.execCommand('copy'); } catch { /* Keep text selected. */ }
    }
    button.textContent = copied ? '已复制' : '已选中，请手动复制';
    setTimeout(() => { button.textContent = '复制命令'; }, 2200);
  });
  block.prepend(button);
});
const search = document.querySelector('#tool-search');
if (search) {
  const tools = [...document.querySelectorAll('details.tool')];
  const count = document.querySelector('#tool-count');
  const empty = document.querySelector('#no-results');
  const update = () => {
    const words = search.value.trim().toLowerCase().split(/\s+/).filter(Boolean);
    let visible = 0;
    for (const tool of tools) {
      tool.hidden = !words.every(word => tool.textContent.toLowerCase().includes(word));
      if (!tool.hidden) visible++;
    }
    count.textContent = `显示 ${visible} / ${tools.length} 个工具`;
    empty.hidden = visible !== 0;
  };
  search.addEventListener('input', update); update();
}
function revealHash() {
  if (!location.hash) return;
  const target = document.getElementById(decodeURIComponent(location.hash.slice(1)));
  if (!target) return;
  if (search && target.matches('details.tool')) {
    search.value = ''; search.dispatchEvent(new Event('input'));
  }
  if (target.matches('details')) target.open = true;
  for (let parent = target.parentElement; parent; parent = parent.parentElement) {
    if (parent.matches('details')) parent.open = true;
  }
  requestAnimationFrame(() => target.scrollIntoView({block: 'start'}));
}
window.addEventListener('hashchange', revealHash); revealHash();
