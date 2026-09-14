// Tema: somente a preferência visual vai para localStorage (nada sensível).
const KEY = "cc-theme";

export function currentTheme() {
  try {
    return localStorage.getItem(KEY) || "system";
  } catch {
    return "system";
  }
}

export function applyTheme(choice) {
  const dark =
    choice === "dark" ||
    (choice !== "light" &&
      window.matchMedia("(prefers-color-scheme: dark)").matches);
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

export function saveTheme(choice) {
  try {
    localStorage.setItem(KEY, choice);
  } catch {
    /* armazenamento indisponível: segue o sistema */
  }
  applyTheme(choice);
}

export function initThemeSection() {
  const current = currentTheme();
  for (const radio of document.querySelectorAll('input[name="theme"]')) {
    radio.checked = radio.value === current;
    radio.addEventListener("change", () => saveTheme(radio.value));
  }
}

export function initTheme() {
  applyTheme(currentTheme());
  try {
    window
      .matchMedia("(prefers-color-scheme: dark)")
      .addEventListener("change", () => applyTheme(currentTheme()));
  } catch {
    /* Safari antigo: sem listener */
  }
}
