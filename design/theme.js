(() => {
  const key = 'cisheng-studio-theme';
  const names = {peach:'暖杏', ocean:'海盐蓝', forest:'薄荷绿', lavender:'暮光紫', night:'深夜', midnight:'深海'};
  const picker = document.getElementById('themePicker');
  const label = document.getElementById('themeLabel');
  const options = [...picker.querySelectorAll('[data-theme-choice]')];
  function apply(name) {
    name = Object.hasOwn(names, name) ? name : 'peach';
    document.body.dataset.theme = name;
    document.body.classList.toggle('dark', name === 'night' || name === 'midnight');
    label.textContent = names[name];
    options.forEach(button => button.setAttribute('aria-pressed', String(button.dataset.themeChoice === name)));
  }
  let saved = null;
  try { saved = localStorage.getItem(key); } catch (_) {}
  apply(saved);
  options.forEach(button => button.addEventListener('click', () => {
    apply(button.dataset.themeChoice);
    try { localStorage.setItem(key, document.body.dataset.theme); } catch (_) {}
    picker.open = false;
    picker.querySelector('summary').focus();
  }));
  document.addEventListener('click', event => { if (!picker.contains(event.target)) picker.open = false; });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && picker.open) { picker.open = false; picker.querySelector('summary').focus(); }
  });
})();
