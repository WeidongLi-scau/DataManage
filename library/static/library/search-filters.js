(() => {
  const group = document.querySelector('#search-group');
  const folder = document.querySelector('#search-folder');
  const status = document.querySelector('#folder-filter-status');
  if (!group || !folder) return;
  const original = Array.from(folder.children, child => child.cloneNode(true));
  const update = (announce = false) => {
    const previous = folder.value;
    const options = original.filter(option => !option.dataset.group || !group.value || option.dataset.group === group.value);
    folder.replaceChildren(...options.map(option => option.cloneNode(true)));
    const retained = Array.from(folder.options).some(option => option.value === previous);
    folder.value = retained ? previous : '';
    if (announce && previous && !retained && status) {
      status.textContent = '已切换项目，文件夹条件已重置。请选择文件夹后应用筛选。';
      status.hidden = false;
    } else if (status) status.hidden = true;
  };
  group.addEventListener('change', () => update(true));
  update();
})();
