document.addEventListener("DOMContentLoaded", () => {
  const template = document.getElementById("rowTemplate");
  const rows = document.getElementById("rows");
  const addButton = document.getElementById("addRow");

  function setupRemoveButton(row) {
    const removeButton = row.querySelector(".remove-row");

    if (removeButton) {
      removeButton.addEventListener("click", () => {
        row.remove();
      });
    }
  }

  function addRow() {
    if (!template || !rows) {
      return;
    }

    const fragment = template.content.cloneNode(true);
    const row = fragment.querySelector(".match-row");

    if (!row) {
      return;
    }

    setupRemoveButton(row);
    rows.appendChild(fragment);
  }

  if (addButton && template && rows) {
    addButton.addEventListener("click", addRow);

    // 新規登録画面など、まだ1行もない場合だけ
    // 最初の入力行を1つ追加する
    if (rows.children.length === 0) {
      addRow();
    }

    // 編集画面に最初から存在する行にも
    // 削除ボタンの処理を設定する
    rows.querySelectorAll(".match-row").forEach((row) => {
      setupRemoveButton(row);
    });
  }

  document.querySelectorAll("[data-copy-target]").forEach((button) => {
    button.addEventListener("click", async () => {
      const target = document.getElementById(
        button.dataset.copyTarget
      );

      if (!target) {
        return;
      }

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


const menuToggle = document.querySelector(".menu-toggle");
const siteNav = document.querySelector(".site-nav");

if (menuToggle && siteNav) {
  menuToggle.addEventListener("click", () => {
    const isOpen = siteNav.classList.toggle("is-open");

    menuToggle.setAttribute(
      "aria-expanded",
      isOpen ? "true" : "false"
    );

    menuToggle.textContent = isOpen ? "×" : "☰";
  });
}