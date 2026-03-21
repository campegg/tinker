/** Home page icon interaction and handle text fade. */

const STATES = {
  mail: "cam@campegg.com",
  mastodon: "@cam@campegg.com",
  bluesky: "@campegg.com",
};

const DEFAULT_ICON = "mail";

let currentIcon = DEFAULT_ICON;
let isFading = false;
let pendingText = null;

const handle = document.querySelector(".home-handle");
const buttons = document.querySelectorAll(".icon-btn[data-icon]");

function setActive(iconName) {
  buttons.forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.icon === iconName);
  });
}

function switchHandle(newText) {
  // Always record the most recently requested text so a click during a
  // fade-out picks up the right string when the transition completes.
  pendingText = newText;

  // If already fading, let the in-flight transitionend handlers pick up
  // pendingText when they finish rather than starting a second sequence.
  if (isFading) return;

  isFading = true;

  // Lock width so the container doesn't reflow while the text is invisible.
  handle.style.minWidth = `${handle.offsetWidth}px`;
  handle.style.opacity = "0";

  handle.addEventListener(
    "transitionend",
    () => {
      // Swap to whatever was most recently requested.
      handle.textContent = pendingText;
      pendingText = null;

      // Force a reflow so the browser commits the text change and
      // opacity=0 before we set opacity=1, ensuring the transition fires.
      void handle.offsetWidth;

      handle.style.opacity = "1";

      handle.addEventListener(
        "transitionend",
        () => {
          handle.style.minWidth = "";
          isFading = false;

          // A click arrived while we were fading in — process it now.
          if (pendingText !== null) {
            const queued = pendingText;
            pendingText = null;
            switchHandle(queued);
          }
        },
        { once: true },
      );
    },
    { once: true },
  );
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
