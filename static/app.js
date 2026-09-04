document.addEventListener("DOMContentLoaded", () => {
  const template = document.getElementById("rowTemplate");
  const rows = document.getElementById("rows");
  const addButton = document.getElementById("addRow");

  function addRow() {
    if (!template || !rows) return;
    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector(".match-row");
    row.querySelector(".remove-row").addEventListener("click", () => row.remove());
    rows.appendChild(fragment);
  }

  if (addButton) {
    addButton.addEventListener("click", addRow);
    addRow();
  }

  document.querySelectorAll("[data-copy-target]").forEach(button => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(button.dataset.copyTarget);
      if (!target) return;
      try {
        await navigator.clipboard.writeText(target.value);
        button.textContent = "コピー済み";
      } catch {
        target.select();
        document.execCommand("copy");
        button.textContent = "コピー済み";
      }
    });
  });
});
