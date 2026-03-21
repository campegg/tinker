/** Home page icon interaction and handle text dissolve. */

const STATES = {
    mail: "cam@campegg.com",
    mastodon: "@cam@campegg.com",
    bluesky: "@campegg.com",
};

const DEFAULT_ICON = "mail";

let currentIcon = DEFAULT_ICON;
let animating = false;

const handle = document.querySelector(".home-handle");
const buttons = document.querySelectorAll(".icon-btn[data-icon]");

function setActive(iconName) {
    buttons.forEach((btn) => {
        btn.classList.toggle("is-active", btn.dataset.icon === iconName);
    });
}

function switchHandle(newText) {
    if (animating) {
        handle.style.minWidth = "";
        handle.classList.remove("is-fading-out", "is-fading-in");
    }

    // Lock width during transition so centering doesn't jump.
    handle.style.minWidth = handle.offsetWidth + "px";

    animating = true;
    handle.classList.add("is-fading-out");

    handle.addEventListener(
        "animationend",
        () => {
            handle.textContent = newText;
            handle.classList.remove("is-fading-out");
            handle.classList.add("is-fading-in");

            handle.addEventListener(
                "animationend",
                () => {
                    handle.classList.remove("is-fading-in");
                    handle.style.minWidth = "";
                    animating = false;
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

// Set initial state.
setActive(DEFAULT_ICON);
