(() => {
  const tree = document.querySelector('#project-folder-tree');
  const button = document.querySelector('.tree-expand-all');
  const toggles = Array.from(tree?.querySelectorAll('.directory-branch-toggle') || []);
  if (!button || !toggles.length) return;
  const setExpanded = (toggle, expanded) => {
    const children = document.getElementById(toggle.getAttribute('aria-controls'));
    if (!children) return;
    children.hidden = !expanded;
    toggle.setAttribute('aria-expanded', String(expanded));
  };
  const sync = () => {
    const expanded = toggles.every(toggle => toggle.getAttribute('aria-expanded') === 'true');
    button.setAttribute('aria-expanded', String(expanded));
    button.textContent = expanded ? '收起全部' : '展开全部';
  };
  toggles.forEach(toggle => {
    setExpanded(toggle, toggle.dataset.initialExpanded === 'true');
    toggle.hidden = false;
    toggle.addEventListener('click', () => {
      setExpanded(toggle, toggle.getAttribute('aria-expanded') !== 'true');
      sync();
    });
  });
  button.hidden = false;
  button.addEventListener('click', () => {
    const expand = button.getAttribute('aria-expanded') !== 'true';
    toggles.forEach(toggle => setExpanded(toggle, expand));
    sync();
  });
  sync();
})();
