/* ==========================================================================
1. DROPDOWN MENU CONTROLLER for ":MORE" button
Handles opening, closing, and event propagation for the navigation menu.
========================================================================== */    

// Fetch the menu trigger button and the target dropdown element from the DOM
const toggleBtn = document.getElementById("menuToggle");
const dropdown = document.getElementById("menuDropdown");

// Toggle dropdown visibility when clicking the menu button
toggleBtn.addEventListener("click", (e) => {
    // Prevent click from bubbling up to document (which would instantly close the menu)
    e.stopPropagation();

    // Toggle the 'show' class to display or hide the menu via CSS
    dropdown.classList.toggle("show");
});

// Prevent clicks inside the dropdown content from bubbling up and closing the menu
dropdown.addEventListener("click", (e) => e.stopPropagation());

// Global click listener: closes the dropdown whenever a click occurs outside the menu
document.addEventListener("click", () => dropdown.classList.remove("show"));

/* ==========================================================================
    2. THEME CONTROLLER & STORAGE
    Manages dark/light theme switching and persists user preferences.
    ========================================================================== */
/**
 * Updates the application's visual theme and saves the preference.
 * @param {string} theme - The target theme name (e.g., 'light', 'dark')
 */
function setTheme(theme) {
    // Apply theme attribute to <html> element to trigger CSS rules (e.g., [data-theme="dark"])
    document.documentElement.setAttribute("data-theme", theme);

    // Store user choice in browser local storage for persistence across reloads
    localStorage.setItem("finman-theme", theme);
}

// Reference the theme selector dropdown (if present on the current page)
const themeSelect = document.getElementById("theme-select");

// Initialize theme selector state if the dropdown exists on this layout
if (themeSelect) {
    // Retrieve previously saved theme preference from local storage
    const saved = localStorage.getItem("finman-theme");

    // Synchronize the HTML select element value with the saved theme
    if (saved) themeSelect.value = saved;

    // Listen for selection changes to update the theme in real time
    themeSelect.addEventListener("change", (e) => {
        // Pass the newly selected value to the setTheme update function
        setTheme(e.target.value);
    });
}

// Price refresh UI
// Immediately gives the user feedback when a price refresh is requested, disables the link to prevent 
// duplicate refresh requests, and then starts the normal server-side /refresh_prices request.
function showLoadingAndNavigate(link, message="⟳ Refreshing prices…") {

    // Show immediate feedback to the user
    link.textContent = message;

    // Prevent another click while the refresh is running
    link.style.pointerEvents = "none";
    link.style.opacity = "0.6";

    // Give the browser time to display the new text
    // before starting the server request.
    setTimeout(function () {
        window.location.href = link.href;
    }, 100);

    // Prevent the original <a> navigation
    return false;
}

// Apply saved theme on every page
(function () {
// Retrieve previously saved theme preference from local storage
    const saved = localStorage.getItem("finman-theme");

    if (saved) {
        // Apply theme attribute to <html> element to trigger CSS rules (e.g., [data-theme="dark"])
        document.documentElement.setAttribute("data-theme", saved);
    }
})();