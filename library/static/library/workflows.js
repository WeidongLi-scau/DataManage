document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector(".bulk-select-form");
  if (!form) return;
  const all = form.querySelector("[data-select-page]");
  const boxes = Array.from(form.querySelectorAll('[name="materials"]'));
  const update = () => {
    const count = boxes.filter((box) => box.checked).length;
    all.checked = count === boxes.length;
    all.indeterminate = count > 0 && count < boxes.length;
    form.querySelector("[data-selection-count]").textContent = `已选 ${count} 项`;
    boxes[0]?.setCustomValidity(count ? "" : "请先选择至少一份资料。");
  };
  all.addEventListener("change", () => { boxes.forEach((box) => { box.checked = all.checked; }); update(); });
  boxes.forEach((box) => box.addEventListener("change", update));
  update();
});
