/** Home page icon interaction and handle text transition. */

const STATES = {
  mail: "cam@campegg.com",
  mastodon: "@cam@campegg.com",
  bluesky: "@campegg.com",
};

const DEFAULT_ICON = "mail";
let currentIcon = DEFAULT_ICON;

const handle = document.querySelector(".home-handle");
const buttons = document.querySelectorAll(".icon-btn[data-icon]");

function setActive(iconName) {
  buttons.forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.icon === iconName);
  });
}

function switchHandle(newText) {
  if (document.startViewTransition) {
    document.startViewTransition(() => {
      handle.textContent = newText;
    });
  } else {
    handle.textContent = newText;
  }
}

buttons.forEach((btn) => {
  btn.addEventListener("click", () => {
    const iconName = btn.dataset.icon;
    if (iconName === currentIcon) return;

    currentIcon = iconName;
    setActive(iconName);
    switchHandle(STATES[iconName]);
  });
});

setActive(DEFAULT_ICON);
